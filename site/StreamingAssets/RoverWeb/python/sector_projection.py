"""Display-only lunar chart; SQLite navigation addresses remain unchanged."""
import copy
import numpy as np


class Frame:
    def __init__(self, value):
        self.address=float(value['address'])
        self.origin=np.array([value['x'],value['z']],dtype=float)
        self.matrix=np.array([[value['xx'],value['xz']],[value['zx'],value['zz']]],dtype=float)
        self.inverse=np.linalg.inv(self.matrix)
        self.scale=float(np.linalg.norm(self.matrix,axis=0).max())

    def project(self, points):
        p=np.asarray(points,dtype=float).copy()
        p[:,:2]=(p[:,:2]-[self.address,0])@self.matrix.T+self.origin
        return p

    def query(self, bounds):
        a,b,c,d=bounds
        corners=(np.array([[a,c],[a,d],[b,c],[b,d]])-self.origin)@self.inverse.T+[self.address,0]
        low=np.maximum(corners.min(axis=0),[self.address-5000,-5000])
        high=np.minimum(corners.max(axis=0),[self.address+5000,5000])
        if np.any(low>high):return None
        return low[0],high[0],low[1],high[1]


def project_points(points, frames):
    result=np.asarray(points,dtype=float).copy()
    if not len(result):return result
    original=result.copy()
    for frame in frames:
        mask=np.isfinite(original[:,0]) & (np.abs(original[:,0]-frame.address)<5000)
        result[mask]=frame.project(original[mask])
    return result


def prepare_snapshot(data):
    if data.get('_global') or not data.get('mapFrames'):return data
    data=copy.deepcopy(data);frames=[Frame(f) for f in data['mapFrames']]
    offset=np.array([data.get('originX',0),data.get('originZ',0),data.get('originY',0)])
    seen=set();points=[]
    def shift(point):
        if not isinstance(point,dict):return
        point=point.get('p',point)
        if id(point) in seen:return
        seen.add(id(point))
        points.append(point)
    rover=data.get('rover',{})
    storage_x=rover.get('x',0)+offset[0]
    for key in ('cloud','cells','path','trajectory','tree','waypoints'):
        for point in data.get(key,[]) or []:shift(point)
    for route in data.get('candidates',[]) or []:
        for point in route.get('points',[]) or []:shift(point)
    shift(rover)
    transformed=project_points(np.array([[p.get('x',0),p.get('z',0),p.get('y',0)] for p in points],dtype=float)+offset,frames)
    for point,p in zip(points,transformed):point.update(x=float(p[0]),z=float(p[1]),y=float(p[2]))
    direction=data.get('forward')
    if isinstance(direction,dict):
        for frame in frames:
            if abs(storage_x-frame.address)<5000:
                v=frame.matrix@np.array([direction.get('x',0),direction.get('z',1)])
                direction.update(x=float(v[0]),z=float(v[1]));break
    data['groundY']=data.get('groundY',0)+offset[2];data['datumY']=data.get('datumY',0)+offset[2]
    data['_global']=True
    return data


class ProjectedArchive:
    def __init__(self, raw):self.raw=raw;self.frames=[]
    def __getattr__(self, name):return getattr(self.raw,name)
    def configure(self, values):self.frames=[Frame(v) for v in (values or [])]

    def _query(self, method, rover, bounds, *args, **kwargs):
        if not self.frames:return getattr(self.raw,method)(rover,bounds,*args,**kwargs)
        results=[];size=.5
        for f in self.frames:
            query=f.query(bounds)
            if query is None:continue
            data,cell=getattr(self.raw,method)(rover,query,*args,**kwargs)
            if len(data):results.append(f.project(data));size=max(size,cell*f.scale)
        return (np.concatenate(results) if results else np.empty((0,6))),size
    def tiles(self, rover, bounds, **kwargs):return self._query('tiles',rover,bounds,**kwargs)
    def surfaces(self, rover, bounds, *args, **kwargs):return self._query('surfaces',rover,bounds,*args,**kwargs)
    def track(self, rover, **kwargs):return project_points(self.raw.track(rover,**kwargs),self.frames)
    def route(self, rover):return project_points(self.raw.route(rover),self.frames)
    def bounds(self, ids):
        if not self.frames:return self.raw.bounds(ids)
        lows=[];highs=[]
        for f in self.frames:
            for rover in ids:
                data,size=self.raw.tiles(rover,(f.address-5000,f.address+5000,-5000,5000),target=128)
                if len(data):
                    p=f.project(data)[:,:3];lows.append(p.min(axis=0)-[size,size,0]);highs.append(p.max(axis=0)+[size,size,0])
        return (np.min(lows,axis=0),np.max(highs,axis=0)) if lows else None
    def nearest(self, ids, x, z, radius):
        if not self.frames:return self.raw.nearest(ids,x,z,radius)
        best=None;distance=float('inf')
        for f in self.frames:
            query=f.query((x-radius,x+radius,z-radius,z+radius))
            if query is None:continue
            a,b,c,d=query;row=self.raw.nearest(ids,(a+b)/2,(c+d)/2,max(b-a,d-c)/2)
            if row:
                p=f.project(np.array([[row[1],row[3],row[2]]]))[0];dist=(p[0]-x)**2+(p[1]-z)**2
                if dist<distance:best=(row[0],p[0],p[2],p[1],*row[4:]);distance=dist
        return best
