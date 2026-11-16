#!/usr/bin/env python3
"""Live viewer: Unity → live.json. It does not control the rover. Plot axes: X, Z, height Y."""
from __future__ import annotations
import argparse
import json
import math
import time
import threading
import textwrap
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D
from matplotlib.widgets import CheckButtons
from matplotlib.colors import ListedColormap, BoundaryNorm, LinearSegmentedColormap
from mpl_toolkits.mplot3d.art3d import Line3DCollection
import importlib.util as _importlib
_projection_spec=_importlib.spec_from_file_location('rover_sector_projection',Path(__file__).with_name('sector_projection.py'))
_projection=_importlib.module_from_spec(_projection_spec);_projection_spec.loader.exec_module(_projection)
prepare_snapshot=_projection.prepare_snapshot

KIND_COLORS = {0: '#818991', 1: '#43b66d', 2: '#ff7f21', 3: '#f03e3e', 4: '#a55af5', 5: '#818991', 6: '#bf2085', 7: '#12bdce'}
KIND_NAMES = {1: 'Safe ground', 7: 'Connected center (last plan)', 2: 'Slope too steep', 3: 'High step / edge', 4: 'Low edge / drop', 5: 'No return: ground not confirmed'}

# Misma escala estable que la nube original del rover: azul/cian, verde,
# amarillo y rojo. No renormalizar cada encuadre: un obstáculo no cambia de
# color al alejarse. El archivo conserva por separado la clasificación física.
HEIGHT_CMAP = LinearSegmentedColormap.from_list('rover_height',
    [(0., '#0000ff'),(.185, '#00ffff'),(.2, '#00ff00'),(.23, '#00ff00'),(.615, '#ffff00'),(1., '#ff0000')])


def xyz(rows):
    """Unity x,y,z → grafico x,z,y. Acepta Vector3 o objetos {p: Vector3}."""
    points = _vectors(rows or [])
    return points[np.isfinite(points).all(axis=1)]


class LiveReader:
    """Conserva la ultima instantanea valida ante reemplazos, archivos parciales o reinicios."""
    def __init__(self, path):
        self.path = Path(path)
        if self.path.is_dir() or self.path.suffix.lower() != '.json':
            self.path = self.path / 'live.json'
        self.signature = None
        self.latest = None
        self.last_success = 0.0
        self.error = 'Waiting for Unity'
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self._pending = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, interval=.5):
        """Un solo lector. Reemplaza el pendiente: nunca acumula una cola de frames."""
        if self.running:
            return
        self._stop.clear()
        def read_latest():
            while not self._stop.is_set():
                data = self.poll()
                if data is not None:
                    with self._lock:
                        self._pending = data
                self._stop.wait(max(.1, interval))
        self._thread = threading.Thread(target=read_latest, daemon=True, name='rover-json')
        self._thread.start()

    def take_latest(self):
        with self._lock:
            data, self._pending = self._pending, None
        return data

    def close(self):
        self._stop.set()

    def poll(self):
        try:
            stat = self.path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            if signature == self.signature:
                return None
            if stat.st_size > 50_000_000:
                raise ValueError('live.json supera 50 MB; desactiva Export Full Cloud y reduce Max Live Cloud Points')
            with self.path.open('r', encoding='utf-8-sig') as f:
                payload = f.read()
            # Liberar el archivo antes de parsear: Windows debe permitir que
            # Unity sustituya la instantanea mientras se procesa una nube grande.
            data = json.loads(payload)
            if not isinstance(data, dict) or data.get('schema') not in (2, 3):
                raise ValueError('Formato incompatible: se requiere schema 2 o 3')
            if not isinstance(data.get('sequence'), int) or not isinstance(data.get('session'), str):
                raise ValueError('Faltan session/sequence')
            for name in ('cloud', 'cells', 'waypoints', 'tree', 'parents', 'candidates', 'path', 'trajectory'):
                if not isinstance(data.get(name, []), list):
                    raise ValueError(f'Campo {name} invalido')
            self.signature = signature
            self.latest = data
            self.last_success = time.monotonic()
            self.error = ''
            return data
        except (OSError, ValueError, json.JSONDecodeError) as e:
            self.error = str(e)
            return None


def demo_snapshot(sequence=1):
    def p(x, y, z): return {'x': x, 'y': y, 'z': z}
    cells = []
    cloud = []
    for x in np.arange(-9, 10, .5):
        for z in np.arange(-8, 9, .5):
            kind = 1
            y = .08*x
            if -1 < x < 1 and -2 < z < 4: kind, y = 2, 1.5
            if 4 < x < 7 and -5 < z < -2: kind, y = 4, -1.5
            if x < -7 and z > 5: kind = 5
            pos = p(float(x), float(y), float(z))
            cells.append({'p': pos, 'kind': kind, 'relativeHeight': y, 'slope': 50 if kind == 2 else 5})
            if kind != 5: cloud.append({'p': pos, 'waypoint': False})
    path = [p(-6, -.48, 0), p(-3, -.24, -4), p(2, .16, -4), p(7, .56, 2)]
    return dict(schema=2, sequence=sequence, session='demo', utc='', time=sequence*.5, mode='auto',
                status='SYNTHETIC DEMO · Sample data to test the viewer', sensorStatus='Example data',
                rover=path[0], forward=p(1, 0, 0), groundY=-.48, datumY=0, bestCost=17.4,
                cellSize=.5, footprintRadius=1, mapAge=.2, revision=sequence, plan=1,
                hasRoute=True, planning=False, cloud=cloud, cells=cells, path=path,
                trajectory=path[:2], tree=path, parents=[-1, 0, 1, 2],
                candidates=[{'points': [path[0], p(-2,0,-6), p(4,0,-6), path[-1]]}],
                waypoints=[{'p': path[-1], 'name': 'Esfera 1', 'collected': False}])


def _vectors(rows):
    """Conservar indices para asociar clases, etiquetas y padres con sus puntos."""
    values = []
    for row in rows:
        try:
            p = row.get('p', row)
            values.append((float(p['x']), float(p['z']), float(p['y'])))
        except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
            values.append((np.nan, np.nan, np.nan))
    return np.asarray(values, dtype=float).reshape(-1, 3)


def limited_line(points, limit):
    """Solo representacion: conservar extremos y limitar vertices de trazas largas."""
    if len(points) <= limit:
        return points
    return points[np.linspace(0, len(points)-1, limit, dtype=int)]


def sampled_cloud(rows, limit=0, voxel=0.):
    """Por defecto conserva todos los retornos. Limite 0 = sin limite; voxel 0 = sin agrupar.

    No promedia alturas ni crea superficies. Nunca modifica la instantanea.
    La reduccion solo sirve para dibujar, no para detectar obstaculos.
    """
    points = _vectors(rows)
    flags = np.array([bool(r.get('waypoint', False)) if isinstance(r, dict) else False for r in rows], dtype=bool)
    valid = np.isfinite(points).all(axis=1)
    points, flags = points[valid], flags[valid]
    bounds = (points.min(axis=0), points.max(axis=0)) if len(points) else None
    if not len(points):
        return points, flags, bounds
    # Las claves permanecen float para evitar overflow int64 en coordenadas anormales.
    if voxel > 0:
        keys = np.column_stack((np.floor(points/max(.01, voxel)), flags))
        _, unique = np.unique(keys, axis=0, return_index=True)
        points, flags = points[unique], flags[unique]
    if limit > 0 and len(points) > limit:
        # Reservar una parte para impactos de objetivos sin exceder el total.
        goal_ids, ground_ids = np.flatnonzero(flags), np.flatnonzero(~flags)
        goal_count = min(len(goal_ids), max(1, limit//5))
        ground_count = min(len(ground_ids), limit-goal_count)
        goal_count = min(len(goal_ids), limit-ground_count)
        def pick(ids, count):
            return ids[np.linspace(0, len(ids)-1, count, dtype=int)] if count else np.empty(0, dtype=int)
        selected = np.concatenate((pick(ground_ids, ground_count), pick(goal_ids, goal_count)))
        points, flags = points[selected], flags[selected]
    return points, flags, bounds


def raster_cells(rows, cell_size=.5, resolution=512, show_reachable=False):
    """Mapa 2D acotado; desconocido transparente y prioridad a clases no transitables.

    No interpola entre celdas. Si el mapa es enorme, agrupa SOLO su representacion.
    """
    points = _vectors(rows)
    valid = np.isfinite(points).all(axis=1)
    ids = np.flatnonzero(valid)
    if not len(ids):
        return np.ma.masked_all((1, 1)), (0., 1., 0., 1.), None
    points = points[valid]
    size = max(.01, float(cell_size))
    keys = np.floor(points[:, :2]/size)
    low_key = keys.min(axis=0)
    span = keys.max(axis=0)-low_key+1
    factor = max(1., float(np.ceil(span.max()/resolution)))
    ij = np.floor((keys-low_key)/factor).astype(int)
    width, height = ij.max(axis=0)+1
    priorities = np.array([0, 1, 5, 6, 7, 4, 3, 2], dtype=np.int8)
    kinds = []
    for i in ids:
        try:
            value = int(rows[i].get('kind', 0))
            if value == 1 and show_reachable and rows[i].get('reachable', False):
                value = 7
            kinds.append(value if 0 <= value <= 7 else 0)
        except (TypeError, ValueError, OverflowError):
            kinds.append(0)
    ranks = np.full((height, width), -1, dtype=np.int8)
    np.maximum.at(ranks, (ij[:, 1], ij[:, 0]), priorities[kinds])
    inverse = np.argsort(priorities)
    raster = np.ma.array(inverse[np.maximum(ranks, 0)], mask=ranks < 0)
    lo = low_key*size
    hi = lo+np.array([width, height])*factor*size
    return raster, (lo[0], hi[0], lo[1], hi[1]), (points.min(axis=0), points.max(axis=0))


class Viewer:
    def __init__(self, reader=None, max_points=0, demo=False, voxel=0., cloud_interval=1.):
        self.archive = None
        self._map_only = False
        self.reader, self.max_points, self.demo = reader, max(0, int(max_points)), demo
        self.voxel, self.cloud_interval = max(0., voxel), max(0., cloud_interval)
        self.fig = plt.figure(figsize=(15, 8.5), facecolor='#f3f6fa')
        self.ax2 = self.fig.add_subplot(121)
        self.ax3 = self.fig.add_subplot(122, projection='3d')
        self.fig.subplots_adjust(left=.06, right=.94, bottom=.32, top=.84, wspace=.16)
        self.fig.suptitle('ROVER · MAP AND PATH PLANNING', x=.06, ha='left', y=.965, fontsize=19, weight='bold')
        self.status = self.fig.text(.06, .919, 'Waiting for the first Unity update', fontsize=13, va='top', color='#087f50', weight='bold')
        self.performance = self.fig.text(.06, .888, '', fontsize=8.5, va='top', color='#234b62')
        self.detail = self.fig.text(.06, .192, '', fontsize=8.5, va='top')
        self.heartbeat = self.fig.text(.06, .042, '', fontsize=13, color='#087f50', weight='bold')
        self.tracking = self.fig.text(.06, .09, '', fontsize=8.5, va='top', color='#234b62')
        self.fig.text(.06, .018, 'Blue: actual track · Green: path · Orange: candidates · Magenta: target', fontsize=9, color='#526172')
        self.ax2.set(xlabel='X [m]', ylabel='Z [m]', title='Vista superior')
        self.ax2.set_aspect('equal', adjustable='box'); self.ax2.grid(alpha=.18)
        self.ax3.set(xlabel='X [m]', ylabel='Z [m]', zlabel='Y · height [m]', title='3D points and paths')
        from matplotlib.ticker import MaxNLocator
        self.ax3.zaxis.set_major_locator(MaxNLocator(4))
        self.ax3.view_init(elev=30, azim=-60)
        self.options = {'Árbol': False, 'Candidatas': True, 'Fit map': True, 'Transitabilidad': True}
        widget_ax = self.fig.add_axes([.79, .055, .17, .16], facecolor=self.fig.get_facecolor())
        self.checks = CheckButtons(widget_ax, list(self.options), list(self.options.values()))
        self.checks.on_clicked(self.toggle)
        self.latest = None
        self.initial = True
        self.tick = 0
        self.timer = None
        self.animation = None  # compatibilidad con llamadas anteriores
        self._session = None
        self._map_key = None
        self._map_bounds = self._cloud_bounds = self._fitted_bounds = None
        self._cloud_time = -float('inf')
        self._heartbeat_time = -float('inf')
        self._display_cloud = np.empty((0, 3))
        self.render_stats = {}

        # Artistas persistentes: no clear(), no recrear ejes ni miles de objetos.
        self.cloud2 = self.ax2.scatter([], [], c=[], cmap=HEIGHT_CMAP, s=2, alpha=.85, linewidths=0, vmin=-2, vmax=8)
        self.cloud3 = self.ax3.scatter([], [], [], c=[], cmap=HEIGHT_CMAP, s=2, alpha=.9, linewidths=0,
                                       depthshade=False, vmin=-1, vmax=1)
        self.wp_cloud2 = self.ax2.scatter([], [], c='#bf2085', s=4, alpha=.6, linewidths=0)
        self.wp_cloud3 = self.ax3.scatter([], [], [], c='#bf2085', s=4, alpha=.6, linewidths=0, depthshade=False)
        cmap = ListedColormap([KIND_COLORS[k] for k in range(8)])
        cmap.set_bad((0, 0, 0, 0))
        self.map_image = self.ax2.imshow(np.ma.masked_all((1, 1)), origin='lower', extent=(0, 1, 0, 1),
            interpolation='nearest', cmap=cmap, norm=BoundaryNorm(np.arange(-.5, 8.5), 8), zorder=0, visible=False)
        self.colorbar = self.fig.colorbar(self.cloud3, cax=self.fig.add_axes([.955, .36, .01, .32]))
        self.colorbar.set_label('Y · height [m]', fontsize=8)
        self.colorbar.ax.tick_params(labelsize=8)
        self.path2, = self.ax2.plot([], [], color='#087f50', lw=3, zorder=6)
        self.path3, = self.ax3.plot([], [], [], color='#087f50', lw=3)
        self.track2, = self.ax2.plot([], [], color='#155fce', lw=1.8, zorder=5)
        self.track3, = self.ax3.plot([], [], [], color='#155fce', lw=1.8)
        self.candidates2 = LineCollection([], colors='#eaa13b', linewidths=1, alpha=.4)
        self.candidates3 = Line3DCollection([np.zeros((2, 3))], colors='#eaa13b', linewidths=1, alpha=.4)
        self.tree2 = LineCollection([], colors='#6b16c9', linewidths=1.2, alpha=.9, zorder=4)
        self.tree3 = Line3DCollection([np.zeros((2, 3))], colors='#6b16c9', linewidths=1.2, alpha=.9)
        self.ax2.add_collection(self.candidates2); self.ax3.add_collection3d(self.candidates3)
        self.ax2.add_collection(self.tree2); self.ax3.add_collection3d(self.tree3)
        self.goals2, self.goals3 = [], []
        for marker, color in [('*', '#bd2375'), ('x', '#777777')]:
            self.goals2.append(self.ax2.scatter([], [], marker=marker, s=100, c=color, zorder=7))
            self.goals3.append(self.ax3.scatter([], [], [], marker=marker, s=55, c=color, depthshade=False))
        self.labels = [self.ax2.annotate('', (0, 0), xytext=(5, 5), textcoords='offset points', fontsize=8, visible=False) for _ in range(16)]
        self.rover2 = self.ax2.scatter([], [], c='#102d47', s=45, zorder=8)
        self.rover3 = self.ax3.scatter([], [], [], c='#102d47', s=35, depthshade=False)
        from matplotlib.patches import FancyArrowPatch, Circle
        self.heading = FancyArrowPatch((0, 0), (0, 0), arrowstyle='-|>', mutation_scale=12, color='#102d47', zorder=8)
        self.ax2.add_patch(self.heading)
        self.footprint = Circle((0, 0), 0, fill=False, edgecolor='#102d47', linewidth=1.2, zorder=8)
        self.ax2.add_patch(self.footprint)
        self.legend = self.fig.legend(handles=[Line2D([], [], marker='s', linestyle='', color=KIND_COLORS[k], label=v)
            for k, v in KIND_NAMES.items()], loc='lower left', bbox_to_anchor=(.05, .215), ncol=3, fontsize=11, markerscale=1.5, frameon=False)
        self.legend.set_visible(False)
        self.probe = self.ax2.annotate('', (0, 0), xytext=(8, 8), textcoords='offset points',
            fontsize=8, visible=False, zorder=20, bbox=dict(boxstyle='round', fc='white', alpha=.95))
        self._cell_lookup = {}
        self._probe_key = None
        self.fig.canvas.mpl_connect('button_press_event', self.inspect_cell)
        self.fig.canvas.mpl_connect('close_event', self.close)
        # El encuadre inicial y el zoom 3D se controlan explicitamente, no por cada artista.
        self.ax2.set_autoscale_on(False); self.ax3.set_autoscale_on(False)

    @staticmethod
    def _scatter3(artist, points):
        artist.set_offsets(points[:, :2])
        artist.set_3d_properties(points[:, 2], zdir='z')

    @staticmethod
    def _line(line2, line3, points):
        line2.set_data(points[:, 0], points[:, 1])
        line3.set_data_3d(points[:, 0], points[:, 1], points[:, 2])

    def toggle(self, name):
        self.options[name] = not self.options[name]
        if name == 'Fit map':
            self._fitted_bounds = None
        if self.latest is not None:
            self.draw(self.latest, force=True)

    def _update_cloud(self, data, now):
        points, flags, self._cloud_bounds = sampled_cloud(data.get('cloud', []), self.max_points, self.voxel)
        self._cloud_received = len(data.get('cloud', []))
        self._cloud_source = data.get('sourceCloudPoints', self._cloud_received)
        self._display_cloud = points
        terrain, targets = points[~flags], points[flags]
        self.cloud2.set_offsets(terrain[:, :2]); self.cloud2.set_array(terrain[:, 2])
        self._scatter3(self.cloud3, terrain); self.cloud3.set_array(terrain[:, 2])
        self.wp_cloud2.set_offsets(targets[:, :2]); self._scatter3(self.wp_cloud3, targets)
        datum = data.get('datumY', 0.)
        for artist in (self.cloud2, self.cloud3):
            artist.set_clim(datum-2., datum+8.)
        self._cloud_time = now
        self._cloud_key = (data.get('session'), data.get('cloudGeneration'),
                           len(data.get('cloud', [])), self.max_points, self.voxel)

    def _update_map(self, data):
        image, extent, self._map_bounds = raster_cells(data.get('cells', []), data.get('cellSize', .5), show_reachable=True)
        self.map_image.set_data(image); self.map_image.set_extent(extent)
        self.render_stats['map_pixels'] = image.size
        size = max(.01, float(data.get('cellSize', .5)))
        self._cell_lookup = {}
        for cell in data.get('cells', []):
            try:
                p = cell['p']
                self._cell_lookup[(math.floor(p['x']/size), math.floor(p['z']/size))] = cell
            except (KeyError, TypeError, ValueError, OverflowError):
                continue

    def inspect_cell(self, event):
        if event.inaxes is not self.ax2 or event.button != 1 or self.latest is None:
            return
        toolbar = getattr(self.fig.canvas, 'toolbar', None)
        if toolbar is not None and getattr(toolbar, 'mode', ''):
            return
        size = max(.01, float(self.latest.get('cellSize', .5)))
        self._probe_key = (math.floor(event.xdata/size), math.floor(event.ydata/size))
        self.probe.xy = (event.xdata, event.ydata)
        right_half = event.xdata > sum(self.ax2.get_xlim())/2
        self.probe.set_ha('right' if right_half else 'left')
        self.probe.set_position((-8 if right_half else 8, 8))
        self._update_probe()
        self.fig.canvas.draw_idle()

    def _update_archive(self, data):
        if data.get('archivePath'):
            try:
                if self.archive is None:
                    import importlib.util
                    spec = importlib.util.spec_from_file_location('rover_archive', Path(__file__).with_name('mapa_archivo.py'))
                    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
                    self.archive = module.ArchiveLayer(self, data['archivePath'])
                    self.archive.set_map_only(self._map_only)
                self.archive.update()
            except (OSError, ValueError, __import__('sqlite3').Error) as error:
                self.heartbeat.set_text('SQLite archive: ' + str(error))

    def _update_probe(self):
        if self._probe_key is None or self.latest is None:
            self.probe.set_visible(False)
            return
        cell = self._cell_lookup.get(self._probe_key)
        if cell is None:
            text = 'Unseen cell: blocked'
            if self.archive is not None:
                x,z=self.probe.xy;span=self.ax2.get_xlim();radius=max(.25,(span[1]-span[0])/300)
                ids=[r[0] for r in self.archive.ids if self.archive.enabled.get((r[0],'map'),True)]
                row=self.archive.archive.nearest(ids,x,z,radius)
                if row:
                    from datetime import datetime,timezone
                    text=(f'Past return · {row[0]}\nX {row[1]:.6f} / Y {row[2]:.6f} / Z {row[3]:.6f} m\n'
                          f'Normal: ({row[4]:.3f}, {row[5]:.3f}, {row[6]:.3f}) · distance {row[7]:.3f} m\n'
                          f'{datetime.fromtimestamp(row[8],timezone.utc):%Y-%m-%d %H:%M:%S} UTC\n'
                          'Stored geometry; current ground still needs checking')
        else:
            kind = int(cell.get('kind', 0))
            label = KIND_NAMES.get(kind, {0:'Unknown',6:'Waypoint'}.get(kind, '?'))
            if cell.get('reason') == 3:
                label = 'Face height not bounded by measured ground'
            if kind == 1 and cell.get('smallStep'):
                label = 'Small passable step'
            if 'relief' in cell:
                text = (f"{label}\nLocal relief: {cell['relief']:.3f} m · ground slope: {cell.get('slope',0):.1f}°\n"
                        f"Limits: climb {self.latest.get('maxStepUp',0):.3f} m / drop {self.latest.get('maxDrop',0):.3f} m")
                reasons = {1:'Measured ground', 2:'Ground is too steep',
                           3:'Ground above/below has not been measured; height is still unknown',
                           4:'Height exceeds the set limit', 5:'Drop is too large',
                           6:'No return', 7:'Waypoint', 8:'Small edge with measured ground on both sides'}
                if cell.get('reason') in reasons:
                    text += '\n' + reasons[cell['reason']]
                if cell.get('riserHeight', -1) >= 0:
                    text += f"\nBounded edge height: {cell['riserHeight']:.3f} m"
                if 'observationAge' in cell:
                    text += (f"\nReading taken {cell['observationAge']:.1f} s "
                             f"(maximum current age: {self.latest.get('surfaceMaxAge',0):.1f} s)")
                if cell.get('mappedOcclusion'):
                    text += (f"\nMapped ground hidden by a small passable step"
                             f"\nHidden area checked {cell.get('occlusionAge',0):.1f} s ago; moving slowly")
            else:
                text = label + '\nRefresh live data to see heights and limits'
        self.probe.set_text(text)
        self.probe.set_visible(True)

    def _update_tree(self, data):
        edges = []
        if self.options['Árbol']:
            nodes = _vectors(data.get('tree', []))
            valid = np.isfinite(nodes).all(axis=1)
            parents = data.get('parents', [])
            ids = np.arange(min(len(nodes), len(parents)))
            if len(ids) > 1500:
                ids = ids[np.linspace(0, len(ids)-1, 1500, dtype=int)]
            for i in ids:
                j = parents[i]
                if isinstance(j, int) and 0 <= j < len(nodes) and valid[i] and valid[j]:
                    edges.append(nodes[[j, i]])
        self.tree2.set_segments([p[:, :2] for p in edges]); self.tree3.set_segments(edges)
        self.render_stats['tree_edges'] = len(edges)

    def _fit(self, bounds):
        low = np.min([b[0] for b in bounds], axis=0)
        high = np.max([b[1] for b in bounds], axis=0)
        # Solo ampliar al aparecer datos nuevos: la camara no salta por el submuestreo.
        if self._fitted_bounds is not None:
            old_low, old_high = self._fitted_bounds
            low, high = np.minimum(low, old_low), np.maximum(high, old_high)
            if np.allclose(low, old_low) and np.allclose(high, old_high):
                return
        self._fitted_bounds = (low.copy(), high.copy())
        spans = np.maximum(high-low, 1.)
        # A thin explored strip still needs a readable map viewport; extending
        # the limits preserves equal metric scale rather than stretching data.
        horizontal=max(spans[0],spans[1]);center2=(low[:2]+high[:2])/2
        spans[:2]=np.maximum(spans[:2],horizontal*.65);low[:2]=center2-spans[:2]/2;high[:2]=center2+spans[:2]/2
        for ax in (self.ax2, self.ax3):
            ax.set_xlim(low[0]-.05*spans[0], high[0]+.05*spans[0])
            ax.set_ylim(low[1]-.05*spans[1], high[1]+.05*spans[1])
        zspan = max(1.2*spans[2], .3*max(spans[:2]))
        center = (low[2]+high[2])/2
        self.ax3.set_zlim(center-zspan/2, center+zspan/2)
        self.ax3.set_box_aspect((1.1*spans[0], 1.1*spans[1], zspan))
        # No view_init aqui: conservar el azimut/elevacion que el usuario eligio.

    def draw(self, data, force=False, redraw=True):
        data=prepare_snapshot(data)
        # Unity mantiene el marco local en float32; el archivo y los plots usan
        # coordenadas globales float64. No desplazar vectores de dirección.
        if not data.get('_global') and any(data.get(k,0) for k in ('originX','originY','originZ')):
            import copy
            data=copy.deepcopy(data)
            shifted=set()
            def shift(point):
                if isinstance(point,dict):
                    point=point.get('p',point)
                    if id(point) in shifted:return
                    shifted.add(id(point))
                    for axis in ('x','y','z'):
                        if axis in point:point[axis]+=data.get('origin'+axis.upper(),0)
            for key in ('cloud','cells','path','trajectory','tree','waypoints'):
                for point in data.get(key,[]):shift(point)
            for route in data.get('candidates',[]):
                for point in route.get('points',[]):shift(point)
            shift(data.get('rover',{}));data['_global']=True
            data['groundY']=data.get('groundY',0)+data.get('originY',0)
            data['datumY']=data.get('datumY',0)+data.get('originY',0)
        now = time.monotonic()
        new_session = data.get('session') != self._session
        if new_session:
            self._session = data.get('session')
            self._map_key = self._fitted_bounds = None
            self._probe_key = None
        self.latest = data
        cloud_key = (self._session, data.get('cloudGeneration'), len(data.get('cloud', [])),
                     self.max_points, self.voxel)
        # Unity conserva puntos inmutables hasta cambiar cloudGeneration. Para
        # productores antiguos sin esa revision mantener el refresco periodico.
        changed = 'cloudGeneration' not in data or cloud_key != getattr(self, '_cloud_key', None)
        if force or new_session or (changed and now-self._cloud_time >= self.cloud_interval):
            self._update_cloud(data, now)
        map_key = (self._session, data.get('revision'), len(data.get('cells', [])), data.get('cellSize'), data.get('plan'),
                   data.get('planningRevision'), data.get('reachableCount'), data.get('connectivityComplete'))
        if map_key != self._map_key:
            self._update_map(data)
            self._map_key = map_key
        classified = self.options['Transitabilidad']
        self.map_image.set_visible(classified)
        self.cloud2.set_visible(not classified); self.wp_cloud2.set_visible(not classified)
        self.legend.set_visible(classified)
        self.footprint.set_visible(classified)
        self.ax2.set_title('Top view · ground safety' if classified else 'Vista superior')
        path = xyz(data.get('path', [])) if data.get('hasRoute') else np.empty((0, 3))
        self._line(self.path2, self.path3, path)  # ruta seleccionada sin simplificar
        trajectory = xyz(data.get('trajectory', []))
        track = limited_line(trajectory, 2000)
        self._line(self.track2, self.track3, track)
        routes = []
        if self.options['Candidatas']:
            for route in data.get('candidates', [])[-8:]:
                if isinstance(route, dict):
                    p = limited_line(xyz(route.get('points', [])), 384)
                    if len(p): routes.append(p)
        self.candidates2.set_segments([p[:, :2] for p in routes]); self.candidates3.set_segments(routes)
        self._update_tree(data)
        goals = data.get('waypoints', [])
        valid_goals = [(g, xyz([g])) for g in goals if isinstance(g, dict) and not g.get('collected',False)]
        valid_goals = [(g, p[0]) for g, p in valid_goals if len(p)]
        for done in (False, True):
            points = np.asarray([p for g, p in valid_goals if bool(g.get('collected')) == done], dtype=float).reshape(-1, 3)
            self.goals2[int(done)].set_offsets(points[:, :2]); self._scatter3(self.goals3[int(done)], points)
        for i, label in enumerate(self.labels):
            label.set_visible(i < len(valid_goals))
            if i < len(valid_goals):
                goal, p = valid_goals[i]
                label.xy = p[:2]
                label.set_text(str(goal.get('name', 'Waypoint'))[:30]+(' ✓' if goal.get('collected') else ''))
        rover = xyz([data.get('rover', {'x':0, 'y':0, 'z':0})])
        if not len(rover): rover = np.zeros((1, 3))
        forward = xyz([data.get('forward', {'x':0, 'y':0, 'z':1})])
        if not len(forward): forward = np.array([[0., 1., 0.]])
        self.rover2.set_offsets(rover[:, :2]); self._scatter3(self.rover3, rover)
        self.heading.set_positions(rover[0, :2], rover[0, :2]+forward[0, :2])
        self.footprint.set_center(rover[0, :2])
        self.footprint.set_radius(max(0., float(data.get('footprintRadius', 0))))
        if self.options['Fit map'] or self.initial or new_session:
            bounds = [b for b in (self._cloud_bounds, self._map_bounds) if b is not None]
            for points in (rover, path, trajectory, np.asarray([p for _, p in valid_goals]).reshape(-1, 3)):
                if len(points): bounds.append((points.min(axis=0), points.max(axis=0)))
            self._fit(bounds)
        self.initial = False
        self.status.set_text(textwrap.fill(f"{data.get('mode', '?').upper()} · Plan {data.get('plan', 0)} · {data.get('status', '')}", 130))
        count = sum(not g.get('collected', False) for g in goals if isinstance(g, dict))
        self.render_stats.update(cloud_points=len(self._display_cloud), trajectory_points=len(track),
            candidate_routes=len(routes), candidate_points=sum(map(len, routes)), selected_path_points=len(path))
        # Contar la instantanea de nube efectivamente dibujada, aunque el estado avance antes.
        received = self._cloud_received
        source = self._cloud_source
        density = 'All received points' if self.max_points == 0 and self.voxel == 0 else 'Optional display sampling'
        diagnostic = data.get('planningDiagnostics') or 'Path details unavailable: update the simulator'
        revision = data.get('planningRevision', 0)
        progress = 'complete' if data.get('connectivityComplete') else 'working / not tried'
        lines = [f"LiDAR: {len(self._display_cloud):,} drawn · {received:,} received · {source:,} stored in Unity",
                 f"{density} · {count} targets waiting · t={data.get('time', 0):.1f}s · {len(data.get('cells', [])):,} cells",
                 f"{data.get('waypointStatus', '')}",
                 f"Connections: scan {revision} ({progress}); current map {data.get('revision', 0)}",
                 textwrap.fill(diagnostic, 108)]
        self.detail.set_text('\n'.join(lines))
        tracking = data.get('trackingStatus', '')
        last = data.get('lastReplanReason', 'no data')
        self.tracking.set_text(textwrap.fill(f"{tracking} · New path searches: {data.get('replanCount', 0)} · Latest: {last}", 108))
        if 'requestedSpeed' in data:
            self.performance.set_text(
                f"Motor {data.get('motorInput',0):.2f} · actual speed {data.get('actualSpeed',0):.2f} / requested {data['requestedSpeed']:.2f} m/s"
                f" · forward factor {data.get('yawThrottleFactor',1):.2f} · frame {data.get('frameMs',0):.0f} / peak {data.get('worstFrameMs',0):.0f} ms"
                f" · JSON {data.get('serializationAndWriteMs',0):.0f} ms (background)")
        else:
            self.performance.set_text('')
        self._update_probe()
        self._update_archive(data)
        if redraw:
            self.fig.canvas.draw_idle()

    def update(self, _=None):
        self.tick += 1
        data = demo_snapshot(self.tick) if self.demo else (self.reader.take_latest() if self.reader.running else self.reader.poll())
        changed = data is not None
        if changed:
            self.draw(data, redraw=False)
        elif self.archive is not None:
            try:
                self.archive.update(); changed=True
            except __import__('sqlite3').Error as error:
                self.heartbeat.set_text('SQLite archive: ' + str(error))
        now = time.monotonic()
        if data is not None or now-self._heartbeat_time >= 1.:
            self._heartbeat_time = now
            if self.demo:
                message = 'DEMO: sample data to test the viewer'
            elif self.reader.latest is None:
                message = f'Waiting for {self.reader.path.name}: {self.reader.error[:100]}'
            else:
                age = now-self.reader.last_success
                message = (f'No new data for {age:.0f} s · Unity is stopped or paused' if age > 2.5
                           else 'Connected · reading the latest available state')
                if self.reader.error: message += ' · last valid reading kept'
            if message != self.heartbeat.get_text():
                self.heartbeat.set_text(message); changed = True
        if changed:
            self.fig.canvas.draw_idle()
        return True  # TimerBase elimina callbacks que devuelven False/0.

    def start(self, interval=500):
        if self.reader is not None:
            self.reader.start(interval/1000)
        self.timer = self.fig.canvas.new_timer(interval=interval)
        self.timer.add_callback(self.update)
        self.timer.start()

    def close(self, _=None):
        if self.archive is not None:self.archive.close();self.archive=None
        if self.timer is not None: self.timer.stop()
        if self.reader is not None: self.reader.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', nargs='?', type=Path, help='live.json or a session folder')
    parser.add_argument('--interval', type=int, default=500, help='Read and refresh interval in ms')
    parser.add_argument('--max-points', type=int, default=0, help='TOTAL visible point limit; 0 keeps all points (default)')
    parser.add_argument('--voxel', type=float, default=0., help='Visible point spacing in meters; 0 turns grouping off (default)')
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--save', type=Path)
    parser.add_argument('--no-show', action='store_true')
    args = parser.parse_args()
    if args.max_points < 0 or args.interval < 100 or not math.isfinite(args.voxel) or args.voxel < 0:
        parser.error('max-points >= 0, interval >= 100 and voxel >= 0')
    if not args.demo and args.path is None:
        import tkinter as tk
        from tkinter import filedialog
        root=tk.Tk(); root.withdraw()
        chosen=filedialog.askdirectory(title='Choose the folder containing live.json')
        root.destroy()
        if not chosen: return
        args.path=Path(chosen)
    reader = None if args.demo else LiveReader(args.path)
    viewer = Viewer(reader, args.max_points, args.demo, voxel=args.voxel)
    if args.save or args.no_show or args.demo:
        viewer.update()
    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        viewer.fig.savefig(args.save, dpi=140)
    if args.no_show:
        plt.close(viewer.fig)
        return
    viewer.start(args.interval)
    plt.show()


if __name__ == '__main__':
    main()
