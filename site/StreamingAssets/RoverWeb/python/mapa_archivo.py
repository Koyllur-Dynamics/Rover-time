"""Memoria cartográfica SQLite y capas de flota para el visor Matplotlib existente.

La pirámide dibuja el mapa vigente; map_points conserva los vóxeles observados.
El archivo points de versiones anteriores se mantiene como histórico compatible.
Coordenadas: X/Z horizontal, Y altura, metros. El visor proyecta los sectores
en el mapa lunar sin modificar las coordenadas internas de SQLite.
"""
import csv
import json
import math
import sqlite3
import time
from pathlib import Path

import numpy as np
from matplotlib.widgets import CheckButtons, Button
import importlib.util as _importlib
_projection_spec=_importlib.spec_from_file_location('rover_sector_projection',Path(__file__).with_name('sector_projection.py'))
_projection=_importlib.module_from_spec(_projection_spec);_projection_spec.loader.exec_module(_projection)
ProjectedArchive=_projection.ProjectedArchive


class MapArchive:
    def __init__(self, path):
        self.path = Path(path)
        self.db = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1.)
        self.db.execute('PRAGMA query_only=ON')
        self._track_cache={}

    def surfaces(self, rover, bounds, cell_size=.5, target=512):
        """Última transitabilidad persistente; LOD visual conservador, sin puntos borrados."""
        xmin,xmax,zmin,zmax=bounds
        level=max(0,min(12,math.ceil(math.log(max(1.,max(xmax-xmin,zmax-zmin)/(cell_size*target)),4))))
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE name='surface_tiles'").fetchone():
            # Compatibilidad con archivos anteriores a la migración del escritor Unity.
            rows=[]
            for x,y,z,text in self.db.execute('SELECT x,y,z,data FROM cells WHERE rover=? AND size=? AND kx BETWEEN ? AND ? AND kz BETWEEN ? AND ? LIMIT 20000',
                (rover,cell_size,math.floor(xmin/cell_size),math.floor(xmax/cell_size),math.floor(zmin/cell_size),math.floor(zmax/cell_size))):
                c=json.loads(text);rows.append((x,z,y,c.get('kind',0),c.get('minY',y),c.get('maxY',y)))
            return np.asarray(rows,dtype=float).reshape(-1,6),cell_size
        while True:
            size=cell_size*4**level
            rows=self.db.execute('SELECT (tx+.5)*?,(tz+.5)*?,sy/n,kind,min_y,max_y FROM surface_tiles '
                'WHERE rover=? AND size=? AND level=? AND tx BETWEEN ? AND ? AND tz BETWEEN ? AND ? LIMIT 20001',
                (size,size,rover,cell_size,level,math.floor(xmin/size),math.floor(xmax/size),math.floor(zmin/size),math.floor(zmax/size))).fetchall()
            if len(rows)<=20000 or level>=12:break
            level+=1
        # La BD almacena prioridad conservadora; convertir a clase del sensor.
        data=np.asarray(rows,dtype=float).reshape(-1,6)
        inverse=np.array([0,1,6,5,2,3,4])
        if len(data):data[:,3]=inverse[data[:,3].astype(int)]
        return data,size

    def rovers(self):
        return self.db.execute('SELECT id,name,seen FROM rovers ORDER BY id').fetchall()

    def bounds(self, ids):
        rows = []
        for rover in ids:
            rows += self.db.execute('SELECT MIN(tx),MAX(tx),MIN(tz),MAX(tz),MIN(min_y),MAX(max_y) '
                                    'FROM tiles WHERE rover=? AND level=6', (rover,)).fetchall()
        # Nivel 6 sirve para buscar el encuadre sin recorrer millones de puntos.
        rows = [r for r in rows if r[0] is not None]
        if not rows:
            return None
        # Coordenadas exactas de las medias de los mosaicos más gruesos; ampliar
        # con las celdas extremas del nivel fino vía índice, sin escanear todo.
        extrema = []
        for rover in ids:
            for order in ('tx ASC', 'tx DESC'):
                r = self.db.execute(f'SELECT tx FROM tiles WHERE rover=? AND level=0 ORDER BY {order} LIMIT 1', (rover,)).fetchone()
                if r: extrema.append((rover, r[0]))
        # Z MIN/MAX se obtiene en la pirámide intermedia; se afina en el viewport.
        xmin = min(v for _, v in extrema)*.5
        xmax = (max(v for _, v in extrema)+1)*.5
        # Un índice específico conserva consultas de extremos Z logarítmicas.
        zs = []
        for rover in ids:
            for order in ('tz ASC', 'tz DESC'):
                r = self.db.execute(f'SELECT tz FROM tiles WHERE rover=? AND level=0 ORDER BY {order} LIMIT 1', (rover,)).fetchone()
                if r: zs.append(r[0])
        return np.array([xmin,min(zs)*.5,min(r[4] for r in rows)]), np.array([xmax,(max(zs)+1)*.5,max(r[5] for r in rows)])

    def tiles(self, rover, bounds, target=128):
        xmin,xmax,zmin,zmax = bounds
        level = max(0, min(12, math.ceil(math.log(max(1.,max(xmax-xmin,zmax-zmin)/(.5*target)), 4))))
        size = .5*4**level
        while True:
            size = .5*4**level
            rows = self.db.execute('SELECT sx/n,sz/n,sy/n,n,min_y,max_y FROM tiles '
                'WHERE rover=? AND level=? AND tx BETWEEN ? AND ? AND tz BETWEEN ? AND ? LIMIT 20001',
                (rover,level,math.floor(xmin/size),math.floor(xmax/size),math.floor(zmin/size),math.floor(zmax/size))).fetchall()
            if len(rows)<=20000 or level>=12: break
            level+=1  # Reducir detalle de toda la región, nunca recortar un lado.
        return np.asarray(rows,dtype=float).reshape(-1,6),size

    def track(self, rover, limit=1600):
        key=(rover,limit);cached=self._track_cache.get(key)
        if cached is not None and time.monotonic()-cached[0]<2:return cached[1]
        ends = self.db.execute('SELECT MIN(id),MAX(id) FROM tracks WHERE rover=?', (rover,)).fetchone()
        if ends[0] is None:
            return np.empty((0,3))
        # Búsquedas por índice de ids espaciados: costo proporcional al dibujo,
        # sin OFFSET creciente ni cargar todo el histórico en RAM.
        ids = np.linspace(ends[0],ends[1],min(limit,ends[1]-ends[0]+1),dtype=np.int64)
        rows, previous, session = [], None, None
        for target in ids:
            row = self.db.execute('SELECT id,x,z,y,session FROM tracks WHERE rover=? AND id>=? ORDER BY id LIMIT 1', (rover,int(target))).fetchone()
            if row and row[0] != previous:
                if session is not None and session != row[4]: rows.append([np.nan]*3)
                rows.append(row[1:4]); previous,session = row[0],row[4]
        result=np.asarray(rows,dtype=float).reshape(-1,3)
        self._track_cache[key]=(time.monotonic(),result)
        return result

    def route(self, rover):
        row = self.db.execute('SELECT data FROM routes WHERE rover=? ORDER BY id DESC LIMIT 1',(rover,)).fetchone()
        if not row: return np.empty((0,3))
        data=json.loads(row[0]);origin=np.array([data.get('originX',0),data.get('originZ',0),data.get('originY',0)])
        return np.asarray([[p['x'],p['z'],p['y']] for p in data.get('points',[])],dtype=float).reshape(-1,3)+origin

    def nearest(self, ids, x, z, radius):
        best=None;distance=float('inf')
        for rover in ids:
            current=self.db.execute("SELECT 1 FROM sqlite_master WHERE name='map_points'").fetchone()
            table='map_points' if current and self.db.execute('SELECT 1 FROM map_points WHERE rover=? LIMIT 1',(rover,)).fetchone() else 'points'
            row=self.db.execute('SELECT rover,x,y,z,nx,ny,nz,range,utc FROM '+table+' WHERE rover=? '
                'AND x BETWEEN ? AND ? AND z BETWEEN ? AND ? ORDER BY (x-?)*(x-?)+(z-?)*(z-?),utc DESC LIMIT 1',
                (rover,x-radius,x+radius,z-radius,z+radius,x,x,z,z)).fetchone()
            if row:
                d=(row[1]-x)**2+(row[3]-z)**2
                if d<distance:best=row;distance=d
        return best

    def export_csv(self, folder):
        folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
        # Un único snapshot de lectura; fetchmany evita cargar el archivo entero.
        self.db.execute('BEGIN')
        try:
            tables=['rovers','points','cells','tracks','routes','collections','metadata']
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='map_points'").fetchone():tables.append('map_points')
            for table in tables:
                cursor=self.db.execute('SELECT * FROM '+table)
                with (folder/(table+'.csv')).open('w',newline='',encoding='utf-8') as file:
                    writer=csv.writer(file);writer.writerow([c[0] for c in cursor.description])
                    while rows:=cursor.fetchmany(8192): writer.writerows(rows)
        finally:self.db.rollback()

    def close(self): self.db.close()


class ArchiveLayer:
    colors=('#246bb2','#df6b22','#6c43b2','#1b8b63','#b13b74','#687b12')
    def __init__(self, viewer, path):
        self.viewer=viewer;self.archive=ProjectedArchive(MapArchive(path));self.artists={};self.enabled={};self.context=None
        self.page=0;self.ids=[];self.checks=[];self.map_only=False;self.fitted=False
        self.widget_axes=[viewer.fig.add_axes([x,.065,.33,.14],facecolor=viewer.fig.get_facecolor()) for x in (.06,.41)]
        self.prev=Button(viewer.fig.add_axes([.06,.025,.055,.026]),'◀')
        self.next=Button(viewer.fig.add_axes([.12,.025,.055,.026]),'▶')
        self.prev.on_clicked(lambda _:self.change_page(-1));self.next.on_clicked(lambda _:self.change_page(1))
        self.prev.ax.set_visible(False);self.next.ax.set_visible(False)
        viewer.detail.set_visible(False);viewer.tracking.set_position((.06,.29))
        viewer.tracking.set_visible(False)
        viewer.heartbeat.set_position((.06,.032))
        for text in viewer.fig.texts:
            if text.get_text().startswith('Azul:'):text.set_visible(False)
        self.note=viewer.fig.text(.06,.265,'',fontsize=8,color='#234b62',visible=False)
        viewer.colorbar.ax.set_visible(True)
        viewer.options['Fit map']=False
        if viewer.checks.get_status()[2]:
            viewer.checks.eventson=False;viewer.checks.set_active(2);viewer.checks.eventson=True

    def change_page(self, delta):
        self.page=(self.page+delta)%max(1,math.ceil(len(self.ids)/3));self.widgets();self.viewer.fig.canvas.draw_idle()

    def widgets(self):
        for checks in self.checks:checks.disconnect_events()
        self.checks=[];self.label_keys={}
        # Four rovers remain directly accessible without pager arrow buttons.
        for column,ax in enumerate(self.widget_axes):
            ax.clear();ax.set_axis_off();labels=[];states=[]
            for rover,name,_ in self.ids[column*2:column*2+2]:
                for kind,prefix in (('map','Map'),('route','Path')):
                    label=f'{prefix} · {name[:22]} [{rover[-6:]}]'
                    labels.append(label);self.label_keys[label]=(rover,kind);states.append(self.enabled.setdefault((rover,kind),True))
            if labels:
                checks=CheckButtons(ax,labels,states);self.checks.append(checks)
                for text in checks.labels:
                    text.set_fontsize(10);rover=self.label_keys[text.get_text()][0]
                    index=next(i for i,r in enumerate(self.ids) if r[0]==rover);text.set_color(self.colors[index%len(self.colors)])
                checks.on_clicked(self.toggle)

    def toggle(self,label):
        key=self.label_keys[label];self.enabled[key]=not self.enabled[key];self.update();self.viewer.fig.canvas.draw_idle()

    def set_map_only(self,value):
        v=self.viewer;self.map_only=value;v.ax3.set_visible(not value);v.colorbar.ax.set_visible(not value)
        v.ax2.set_position([.06,.36,.88,.48] if value else [.06,.36,.4074,.48])
        v.ax3.set_position([.5326,.36,.4074,.48])
        v.ax2.set_aspect('auto' if value else 'equal',adjustable='box')
        self.match_aspect()
        v.fig.canvas.draw_idle()

    def match_aspect(self):
        if not self.map_only:return
        ax=self.viewer.ax2;box=ax.get_position();fig=self.viewer.fig
        ratio=box.width*fig.bbox.width/(box.height*fig.bbox.height)
        xlo,xhi=ax.get_xlim();zlo,zhi=ax.get_ylim();width=xhi-xlo;height=zhi-zlo
        if width<height*ratio:
            center=(xlo+xhi)/2;ax.set_xlim(center-height*ratio/2,center+height*ratio/2)
        elif height<width/ratio:
            center=(zlo+zhi)/2;ax.set_ylim(center-width/ratio/2,center+width/ratio/2)

    def fit(self, overview=True):
        bounds=self.archive.bounds([r[0] for r in self.ids if self.enabled.get((r[0],'map'),True)]) if overview else None
        if not overview:
            rover=self.viewer.latest.get('rover',{});p=np.array([rover.get('x',0),rover.get('z',0),rover.get('y',0)])
            bounds=(p-[70,70,3],p+[70,70,6])
        if bounds is not None:
            self.viewer._fitted_bounds=None;self.viewer._fit([bounds]);self.fitted=True
            self.match_aspect()

    def update(self):
        v=self.viewer;rovers=self.archive.rovers()
        self.archive.configure(v.latest.get('mapFrames'))
        context=(v.latest.get('sectorId'),v.latest.get('session'))
        if context!=self.context:self.context=context;self.fitted=False
        if [(r[0],r[1]) for r in rovers]!=[(r[0],r[1]) for r in self.ids]:self.ids=rovers;self.widgets()
        if not self.fitted or v.options['Fit map']:self.fit(v.options['Fit map'] or not self.archive.frames)
        bounds=(*v.ax2.get_xlim(),*v.ax2.get_ylim());total=0;smallest=None;heights=[]
        visible_count=max(1,sum(self.enabled.get((r[0],'map'),True) for r in self.ids))
        for index,(rover,name,seen) in enumerate(self.ids):
            if rover not in self.artists:
                color=self.colors[index%len(self.colors)]
                cloud2=v.ax2.scatter([],[],s=4,c=[],cmap=v.cloud3.get_cmap(),alpha=.95,linewidths=0,zorder=1)
                cloud3=v.ax3.scatter([],[],[],s=5,c=[],cmap=v.cloud3.get_cmap(),alpha=.95,depthshade=False,linewidths=0)
                path2,=v.ax2.plot([],[],color=color,lw=2.5,zorder=6);path3,=v.ax3.plot([],[],[],color=color,lw=2.5)
                track2,=v.ax2.plot([],[],color=color,lw=1,alpha=.8,ls='--',zorder=5);track3,=v.ax3.plot([],[],[],color=color,lw=1,alpha=.8,ls='--')
                marker=v.ax2.annotate(name,(0,0),xytext=(5,5),textcoords='offset points',fontsize=8,color=color)
                collected=v.ax2.scatter([],[],c=color,marker='*',s=65,zorder=7)
                surface=v.ax2.imshow(np.ma.masked_all((1,1)),origin='lower',interpolation='nearest',
                    cmap=v.map_image.get_cmap(),norm=v.map_image.norm,zorder=0)
                obstacles=v.ax3.scatter([],[],[],s=12,c=[],depthshade=False,linewidths=0)
                self.artists[rover]=(cloud2,cloud3,path2,path3,track2,track3,marker,collected,surface,obstacles)
            c2,c3,p2,p3,t2,t3,marker,collected,surface,obstacles=self.artists[rover]
            show_map=self.enabled.get((rover,'map'),True);show_route=self.enabled.get((rover,'route'),True)
            c2.set_visible(show_map and not v.options['Transitabilidad']);c3.set_visible(show_map)
            surface.set_visible(show_map and v.options['Transitabilidad']);obstacles.set_visible(show_map)
            for a in (p2,p3,t2,t3,marker,collected):a.set_visible(show_route)
            if show_map:
                points,size=self.archive.tiles(rover,bounds,target=max(2,int(256/math.sqrt(visible_count))));total+=len(points);smallest=size if smallest is None else min(smallest,size)
                c2.set_offsets(points[:,:2]);v._scatter3(c3,points[:,:3]);c2.set_array(points[:,2]);c3.set_array(points[:,2])
                if len(points):heights.append((points[:,4].min(),points[:,5].max()))
                cells,cell_size=self.archive.surfaces(rover,bounds,v.latest.get('cellSize',.5),target=512)
                raster,extent=surface_raster(cells,cell_size)
                surface.set_data(raster);surface.set_extent(extent)
                blocked=cells[np.isin(cells[:,3],(2,3,4))]
                positions=blocked[:,:3].copy()
                if len(positions):positions[:,2]=blocked[:,5]+.03
                v._scatter3(obstacles,positions)
                palette={2:'#ff7f21',3:'#f03e3e',4:'#a55af5'}
                obstacles.set_facecolors([palette[int(kind)] for kind in blocked[:,3]])
            if show_route:
                route=self.archive.route(rover);v._line(p2,p3,route)
                track=self.archive.track(rover,limit=max(2,2400//max(1,len(self.ids))));v._line(t2,t3,track)
                if len(track):marker.xy=track[-1,:2]
                # El registro de recolección es una auditoría, no un objetivo en el mapa.
                collected.set_offsets(np.empty((0,2)));collected.set_visible(False)
        datum=v.latest.get('datumY',0.)
        for artists in self.artists.values():
            for artist in artists[:2]:artist.set_clim(datum-2.,datum+8.)
        v.cloud3.set_clim(datum-2.,datum+8.)
        v.map_image.set_zorder(2) # Evidencia local/centros conectados encima del histórico.
        v.map_image.set_visible(v.options['Transitabilidad'] and self.enabled.get((v.latest.get('roverId',''),'map'),True))
        # Las capas actuales siguen disponibles para inspección de transitabilidad;
        # nube y trayectorias completas provienen del mismo archivo de todos los rovers.
        for a in (v.cloud2,v.cloud3,v.wp_cloud2,v.wp_cloud3,v.path2,v.path3,v.track2,v.track3):a.set_visible(False)
        self.note.set_text(f'Current map · {total:,} cells drawn · display resolution {smallest or .5:g} m · '
                           f'{len(self.ids)} rovers · height: blue–green–yellow–red · ground safety stored in SQLite')
        v.render_stats['archive_tiles']=total

    def close(self):self.archive.close()


def surface_raster(cells,size):
    if not len(cells):return np.ma.masked_all((1,1)),(0,1,0,1)
    keys=np.floor(cells[:,:2]/size);low=keys.min(axis=0);span=keys.max(axis=0)-low+1
    factor=max(1.,np.ceil(span.max()/1024));ij=np.floor((keys-low)/factor).astype(int)
    priorities=np.array([0,1,4,5,6,3,2]);ranks=np.full(tuple((ij.max(axis=0)+1)[::-1]),-1,dtype=np.int8)
    np.maximum.at(ranks,(ij[:,1],ij[:,0]),priorities[cells[:,3].astype(int)])
    image=np.ma.array(np.argsort(priorities)[np.maximum(ranks,0)],mask=ranks<0)
    lo=low*size;hi=lo+np.array(image.shape[::-1])*size*factor
    return image,(lo[0],hi[0],lo[1],hi[1])


def main():
    import argparse
    parser=argparse.ArgumentParser(description='Export the full map to CSV, without display sampling.')
    parser.add_argument('database',type=Path);parser.add_argument('--export-csv',type=Path,required=True)
    args=parser.parse_args();archive=MapArchive(args.database)
    try:archive.export_csv(args.export_csv)
    finally:archive.close()


if __name__=='__main__':main()
