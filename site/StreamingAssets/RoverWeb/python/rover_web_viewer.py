"""Browser adapter; all plots and interactions stay in the existing viewers."""
import os, json, io, zipfile, time
os.environ['MPLBACKEND']='Agg'
import matplotlib
matplotlib.use('Agg')
import numpy as np
from pathlib import Path
import visor_unity as bridge
import ver_mision_rover as mission
import ver_algoritmos_rover as algorithms
viewer=None
paused=False

def create(mode,live_path):
    global viewer,paused
    if viewer is not None: viewer.close()
    viewer=algorithms.Viewer() if mode=='algorithms' else mission.Viewer(mission.LiveReader(live_path),max_points=0,voxel=0.)
    # Agg has no GUI event loop. The JS worker owns refresh scheduling.
    viewer.fig.canvas.draw_idle=lambda: None
    paused=False

def command(encoded):
    global paused
    cmd=json.loads(encoded)
    if cmd.get('type')=='pause':paused=bool(cmd.get('value'));return
    if cmd.get('type')=='export':return export_csv()
    bridge.dispatch(viewer,cmd)

def render():
    if not paused:viewer.update()
    canvas=viewer.fig.canvas;canvas.draw();width,height=canvas.get_width_height()
    return width,height,np.asarray(canvas.buffer_rgba())[::-1].tobytes(),int((viewer.latest or {}).get('sequence',-1))

def render_png():
    if not paused:viewer.update()
    stream=io.BytesIO();viewer.fig.canvas.print_png(stream)
    return 0,0,stream.getvalue(),int((viewer.latest or {}).get('sequence',-1))

def export_csv():
    if getattr(viewer,'archive',None) is None:raise RuntimeError('Wait for the first map before exporting.')
    folder=Path('/exports')/('rover-'+str(time.time_ns()));folder.mkdir(parents=True)
    viewer.archive.archive.export_csv(folder)
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
        for file in folder.rglob('*'):
            if file.is_file():archive.write(file,str(file.relative_to(folder)))
    for file in sorted(folder.rglob('*'),reverse=True):
        if file.is_file():file.unlink()
        elif file.is_dir():file.rmdir()
    folder.rmdir()
    return stream.getvalue()


def database_will_change():
    layer=getattr(viewer,'archive',None)
    if layer is not None:layer.archive.raw.db.close()

def database_did_change():
    # SQLite cannot observe writes made by a different WASM runtime. Reopen its
    # read-only connection after an exact page update; keep every artist/widget.
    layer=getattr(viewer,'archive',None)
    if layer is None:return
    import sqlite3
    raw=layer.archive.raw
    raw.db=sqlite3.connect(raw.path.resolve().as_uri()+'?mode=ro',uri=True,timeout=1.)
    raw.db.execute('PRAGMA query_only=ON')
    raw._track_cache.clear()
