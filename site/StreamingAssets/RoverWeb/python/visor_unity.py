#!/usr/bin/env python3
"""Transporta el Viewer existente a Unity: Agg -> PNG por stdout, eventos por stdin.

No contiene algoritmos de graficado ni abre una ventana de escritorio.
El protocolo es RVF1 + longitud uint32 + secuencia int32 little-endian + PNG.
"""
import os
os.environ['MPLBACKEND'] = 'Agg'  # antes de importar el visor y pyplot
import argparse
import importlib.util
import io
import json
from pathlib import Path
import queue
import struct
import sys
import threading
import time
from matplotlib.backend_bases import MouseEvent


def load_viewer(path):
    spec = importlib.util.spec_from_file_location('existing_rover_viewer', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dispatch(viewer, command):
    """Reenvia los eventos a los mismos widgets y ejes de Matplotlib."""
    if hasattr(viewer, 'handle_command'):
        viewer.handle_command(command)
        return
    canvas = viewer.fig.canvas
    width, height = canvas.get_width_height()
    kind = command.get('type')
    names = {'down': 'button_press_event', 'up': 'button_release_event',
             'move': 'motion_notify_event', 'scroll': 'scroll_event'}
    if kind == 'fit':
        if viewer.archive is not None:
            viewer.archive.fit();viewer.archive.update();canvas.draw_idle();return
        viewer._fitted_bounds = None
        viewer.initial = True
        if viewer.latest is not None:
            viewer.draw(viewer.latest)
        return
    if kind == 'map':
        viewer._map_only=bool(command.get('value'))
        if viewer.archive is not None:viewer.archive.set_map_only(viewer._map_only)
        return
    if kind == 'export' and viewer.archive is not None:
        if getattr(viewer,'_exporting',False):return
        viewer._exporting=True
        def export():
            import uuid
            archive=type(viewer.archive.archive)(viewer.archive.archive.path)
            root=archive.path.parent
            if root.parent.name=='Sessions':root=root.parent.parent
            folder=root/'exports'/(time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6])
            partial=folder.with_name(folder.name+'.partial')
            try:
                archive.export_csv(partial);partial.rename(folder);viewer._export_message='CSV exported: '+str(folder)
            except Exception as error:viewer._export_message='Export failed: '+str(error)
            finally:archive.close();viewer._exporting=False
        threading.Thread(target=export,daemon=True,name='export-csv').start()
        return
    if kind not in names:
        return
    x, y = float(command.get('x', 0)) * width, float(command.get('y', 0)) * height
    button = int(command.get('button', 1))
    event = MouseEvent(names[kind], canvas, x, y, button=button,
                       step=float(command.get('step', 0)))
    canvas.callbacks.process(event.name, event)
    if kind=='down' and event.inaxes is viewer.ax2:
        viewer._map_drag=(x,y,viewer.ax2.get_xlim(),viewer.ax2.get_ylim())
    if kind=='up':viewer._map_drag=None
    drag=getattr(viewer,'_map_drag',None)
    if kind=='move' and drag is not None:
        sx,sy,xlim,ylim=drag;ax=viewer.ax2
        dx=(x-sx)/ax.bbox.width*(xlim[1]-xlim[0]);dy=(y-sy)/ax.bbox.height*(ylim[1]-ylim[0])
        ax.set_xlim(xlim[0]-dx,xlim[1]-dx);ax.set_ylim(ylim[0]-dy,ylim[1]-dy)
        viewer.options['Fit map']=False
    # Agg carece de la barra de herramientas de escritorio. La rueda opera
    # sobre los limites de los mismos ejes, sin volver a dibujar datos en Unity.
    if kind == 'scroll' and event.inaxes in (viewer.ax2, viewer.ax3):
        viewer.options['Fit map']=False
        factor = 1.2 ** (-max(-10, min(10, event.step)))
        ax = event.inaxes
        for getter, setter in ((ax.get_xlim, ax.set_xlim), (ax.get_ylim, ax.set_ylim)):
            lo, hi = getter(); center = (lo + hi) / 2
            setter(center + (lo-center)*factor, center + (hi-center)*factor)
        if ax is viewer.ax3:
            lo, hi = ax.get_zlim(); center = (lo + hi) / 2
            ax.set_zlim(center + (lo-center)*factor, center + (hi-center)*factor)
    canvas.draw_idle()


def png_frame(viewer):
    buffer = io.BytesIO()
    viewer.fig.canvas.print_png(buffer, pil_kwargs={'compress_level': 1})
    return buffer.getvalue()


def write_frame(stream, png, sequence=-1):
    stream.write(struct.pack('<4sIi', b'RVF1', len(png), sequence))
    stream.write(png)
    stream.flush()


def write_rgba_frame(stream, viewer, sequence=-1):
    """Los mismos píxeles Agg, sin compresión/descompresión PNG en cada frame."""
    import numpy as np
    canvas=viewer.fig.canvas;canvas.draw();width,height=canvas.get_width_height()
    # Texturas Unity: fila inferior primero. Invertir aquí, fuera de la simulación.
    pixels=np.asarray(canvas.buffer_rgba())[::-1].tobytes()
    stream.write(struct.pack('<4sIiII',b'RVF2',len(pixels),sequence,width,height))
    stream.write(pixels);stream.flush()


def serve(viewer, source=sys.stdin, destination=None, interval=.5, raw_rgba=False):
    destination = destination if destination is not None else sys.stdout.buffer
    commands = queue.Queue(maxsize=128)
    stopped = threading.Event()

    def receive():
        try:
            for line in source:
                try:
                    command = json.loads(line)
                    if not isinstance(command, dict):
                        continue
                except (ValueError, TypeError):
                    continue
                # La cola acotada conserva presion/liberacion: el lector espera
                # si es necesario. Unity agrupa los movimientos del puntero.
                while not stopped.is_set():
                    try:
                        commands.put(command, timeout=.1)
                        break
                    except queue.Full:
                        pass
        finally:
            stopped.set()  # tambien finaliza al cerrar Unity o al perder stdin

    threading.Thread(target=receive, daemon=True, name='unity-input').start()
    dirty = [True]
    viewer.fig.canvas.draw_idle = lambda: dirty.__setitem__(0, True)
    paused, next_poll, next_render = False, 0., 0.
    if viewer.reader is not None:
        viewer.reader.start(interval)
    try:
        while not stopped.is_set():
            for _ in range(128):
                try:
                    command = commands.get_nowait()
                except queue.Empty:
                    break
                kind = command.get('type')
                if kind == 'quit':
                    stopped.set()
                    break
                if kind == 'pause':
                    paused = bool(command.get('value'))
                    dirty[0] = True
                else:
                    dispatch(viewer, command)
            now = time.monotonic()
            if not paused and now >= next_poll:
                viewer.update()
                if getattr(viewer,'_export_message',None):viewer.heartbeat.set_text(viewer._export_message)
                next_poll = now + interval
            if not paused and dirty[0] and now >= next_render and not stopped.is_set():
                dirty[0] = False
                sequence=(viewer.latest or {}).get('sequence',-1)
                if raw_rgba:write_rgba_frame(destination,viewer,sequence)
                else:write_frame(destination,png_frame(viewer),sequence)
                next_render = time.monotonic() + .1  # no cola de imagenes atrasadas
            stopped.wait(.02)
    finally:
        viewer.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    parser.add_argument('--viewer', type=Path, default=Path(__file__).with_name('ver_mision_rover.py'))
    parser.add_argument('--interval', type=int, default=500)
    parser.add_argument('--raw-rgba', action='store_true')
    args = parser.parse_args()
    if args.interval < 100:
        parser.error('interval >= 100')
    module = load_viewer(args.viewer)
    viewer = module.Viewer(module.LiveReader(args.path), max_points=0, voxel=0.)
    try:
        serve(viewer, interval=args.interval/1000,raw_rgba=args.raw_rgba)
    except (BrokenPipeError, OSError):
        pass  # el host cerro las tuberias
    finally:
        module.plt.close(viewer.fig)


if __name__ == '__main__':
    main()
