"""Lecciones locales animadas. Mismo transporte Agg/RGBA que el visor de mision.

Ejemplos didacticos, no telemetria ni ordenes para el rover. Sin red ni dependencias
nuevas. Las ecuaciones siguen las implementaciones C# indicadas en cada ficha.
"""
import time
import textwrap
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Rectangle
from matplotlib.collections import LineCollection

BG, PANEL, INK = '#101c2b', '#16283c', '#e7f1fa'
GREEN, GOLD, BLUE, RED = '#52e0ad', '#ffd079', '#71bbff', '#ff7889'

# titulo, explicacion, ecuacion, variables, enlace con codigo/referencia
LESSONS = [
    ('RRT*: build and improve a path',
     'Pick a point, grow the tree, and find the shortest safe connection. A node can switch to a new parent if that makes its path shorter and clear.',
     r'$c(x)=\min_{p\in N(x)}[c(p)+\|x-p\|_2]$',
     'c: total length [m] · N: neighbors with clear connections · Red: obstacle',
     ''),
    ('Seen targets and the rover team',
     'Only targets seen by LiDAR can be chosen. The rover picks the closest one and keeps it while following the path. If no path connects, it tries the next target.',
     r'$g^*=\arg\min_{g\in G_{seen}}\sqrt{(x_g-x_r)^2+(z_g-z_r)^2}$',
     'r: rover · g: target · Gray: not yet seen · Horizontal distance [m]',
     ''),
    ('Following a path: Pure Pursuit',
     'Look at a point ahead on the path. Its position tells the rover how much to turn. The rover slows down on tight turns.',
     r'$\kappa=2\sin(\alpha)/L_d,\quad\delta=\arctan(L\kappa),\quad v\leq\sqrt{a_{lat}/|\kappa|}$',
     'Ld: distance to the point [m] · L: wheelbase [m] · alpha: angle · delta: steering',
     ''),
    ('Braking, rover size, and fresh readings',
     'The whole rover must fit, not just its center. It must be able to stop before reaching ground that has not been checked. An old reading is not enough.',
     r'$d_{stop}=v\,t_r+v^2/(2a)+0.25,\qquad r_{safe}=r_{rover}+margin$',
     'v [m/s] · tr: reaction time [s] · a: slowing down [m/s²] · Distances [m]',
     ''),
    ('LiDAR: the first return',
     'Each ray starts at the sensor and keeps the first hit outside the rover. It does not invent what is hidden behind objects. Hits are grouped into map cells.',
     r'$p=o+d\,\hat{u},\qquad (i,j)=(\lfloor x/h\rfloor,\lfloor z/h\rfloor)$',
     'o: start · u: unit direction · d: range [m] · h: cell size [m]',
     ''),
    ('Safe ground: slopes and steps',
     'The surface direction gives the slope. A step is accepted only when ground is measured on both sides and its height is within the limit. A wall with no ground above stays blocked.',
     r'$\theta=\arccos(\hat{n}\cdot\hat{y}),\quad\Delta h=h_{high}-h_{low}$',
     'theta [degrees] · Δh [m] · Climbing and dropping have separate limits and tolerances',
     ''),
    ('Shared memory and mapping again',
     'SQLite keeps cells, heights, rover IDs, and dates. Rovers share what they see. A new reading updates changed areas. Drawing fewer cells does not erase the map.',
     r'$M[k]\leftarrow observation_{new},\quad k=(world,rover,i,j)$',
     'Example key · Versions and dates separate stored memory from fresh readings',
     ''),
    ('Suspension: a spring and a damper',
     'Each wheel receives a support force. The spring responds to compression, and the damper reduces bouncing. This time step uses mass/4 as an estimate, not a full model of every part.',
     r'$F=\frac{kx-(c+k\Delta t)v+(c+k\Delta t)\Delta t\,g}{1+(c+k\Delta t)\Delta t/m}$',
     'k [N/m] · c [N s/m] · x [m] · v [m/s] · m: mass per wheel · F is limited',
     ''),
    ('Grip, steering, and turning in place',
     'The motor changes wheel speed, while grip reduces sliding. Friction limits the combined force. To turn in place, the two sides spin in opposite directions.',
     r'$\|F_{tire}\|\leq\mu N,\qquad v_{contact}=v_{center}+\omega\times r$',
     'mu: friction coefficient · N: normal load [N] · This is not a full lunar soil model',
     ''),
    ('Following the Sun: two axes',
     'Find the Sun direction relative to the rover body, then calculate its direction and tilt. Motors turn the panel toward the Sun within their limits.',
     r'$\psi=\mathrm{atan2}(s_x,s_z),\quad\beta=\mathrm{atan2}(\sqrt{s_x^2+s_z^2},s_y)$',
     's: local Sun direction · Light alignment = max(0, n·s) · This is not electrical power',
     ''),
    ('Drawing the map: levels of detail',
     'Zooming out groups cells so fewer need to be drawn. Obstacles remain marked. Path planning and SQLite keep the full data.',
     r'$h_{view}=q\,h,\qquad (I,J)=(\lfloor i/q\rfloor,\lfloor j/q\rfloor)$',
     'q: display grouping · h: original resolution · Fewer visible points does not mean less stored data',
     ''),
]


LESSONS.extend([
 ('Arm position: a flat two-link example', 'Joint angles set the tip position. This is a simple example. The mission arm has six moving joints.', r'$x=l_1\cos q_1+l_2\cos(q_1+q_2),\quad y=l_1\sin q_1+l_2\sin(q_1+q_2)$', 'Lengths are in meters; calculations use angles in radians.', ''),
 ('Smooth arm movement', 'Taking more time reduces speed and acceleration. This motion curve does not calculate motor torque.', r'$q=q_0+(q_f-q_0)(3u^2-2u^3),\quad u=t/T$', 'u: time from 0 to 1; position, speed, and acceleration are shown on a relative scale.', '')
])

def edge_free(a, b):
    p = np.linspace(a, b, max(2, int(np.linalg.norm(b-a)/.08)+1))
    return not np.any((p[:, 0] > 4) & (p[:, 0] < 6) & (p[:, 1] > 2) & (p[:, 1] < 7))


def build_tree():
    """RRT* 2D reducido, determinista y precalculado; costes actualizan descendientes."""
    rng = np.random.default_rng(18)
    points, parents, costs, frames = [np.array([1., 1.])], [-1], [0.], []
    goal = np.array([9., 9.])
    for iteration in range(240):
        sample = goal if iteration % 7 == 0 else rng.uniform(.4, 9.6, 2)
        distances = np.linalg.norm(np.array(points)-sample, axis=1)
        nearest = int(np.argmin(distances))
        delta = sample-points[nearest]
        point = points[nearest]+delta*min(1., .8/max(1e-8, distances[nearest]))
        if distances[nearest] < 1e-6 or not edge_free(points[nearest], point):
            continue
        neighbors = [i for i, p in enumerate(points) if np.linalg.norm(p-point) < 1.8 and edge_free(p, point)]
        parent = min(neighbors, key=lambda i: costs[i]+np.linalg.norm(points[i]-point))
        points.append(point); parents.append(parent); costs.append(costs[parent]+np.linalg.norm(points[parent]-point))
        added = len(points)-1; rewired = []
        for i in neighbors:
            new_cost = costs[added]+np.linalg.norm(points[i]-point)
            if new_cost+1e-6 >= costs[i]:
                continue
            # El coste positivo impide reconectar un ancestro a su descendiente.
            change = new_cost-costs[i]; parents[i] = added; costs[i] = new_cost; rewired.append(i)
            todo = [i]
            while todo:
                ancestor = todo.pop()
                for child, p in enumerate(parents):
                    if p == ancestor:
                        costs[child] += change; todo.append(child)
        frames.append((np.array(points).copy(), parents.copy(), costs.copy(), rewired, sample.copy()))
    return frames


def suspension_curve():
    dt, m, k, damping, g = .02, 50., 2200., 380., 1.62
    x, v = .12, 0.; positions = []
    for _ in range(240):
        cd = damping+k*dt
        force = max(0., (k*x-cd*v+cd*dt*g)/(1+cd*dt/m))
        v += (force/m-g)*dt; x -= v*dt
        positions.append(x)
    return np.arange(240)*dt, np.array(positions)


def LiveReader(_path):
    return None  # No lee ni modifica archivos de mision.


class Viewer:
    def __init__(self, reader=None, max_points=0, voxel=0.):
        self.reader = None; self.latest = {'sequence': 0}
        self.topic = 0; self.elapsed = 0.; self.playing = True; self.speed = 1.
        self.last = time.monotonic(); self.needs_draw = True
        self.frames = build_tree(); self.suspension = suspension_curve()
        self.fig = plt.figure(figsize=(9.6, 6), dpi=100, facecolor=BG)
        self.draw()

    def handle_command(self, cmd):
        kind = cmd.get('type')
        if kind == 'topic':
            index = cmd.get('index')
            if not isinstance(index, int) or not 0 <= index < len(LESSONS):
                return
            self.topic = index; self.elapsed = 0.
        elif kind == 'animate':
            self.playing = bool(cmd.get('value'))
        elif kind == 'restart':
            self.elapsed = 0.
        elif kind == 'step':
            self.playing = False; self.elapsed += .35
        elif kind == 'speed':
            self.speed = max(.25, min(2., float(cmd.get('value', 1))))
        else:
            return
        self.last = time.monotonic(); self.needs_draw = True

    def update(self):
        now = time.monotonic()
        if self.playing:
            self.elapsed += min(.5, now-self.last)*self.speed
            self.needs_draw = True
        self.last = now
        if self.needs_draw:
            self.draw(); self.needs_draw = False
            self.latest['sequence'] += 1
            self.fig.canvas.draw_idle()

    def axes(self, xlabel='x [m]', ylabel='z [m]'):
        ax = self.fig.add_axes([.09, .14, .85, .43], facecolor=PANEL)
        ax.tick_params(colors=INK, labelsize=10)
        for spine in ax.spines.values(): spine.set_color('#496078')
        ax.set_xlabel(xlabel, color=INK); ax.set_ylabel(ylabel, color=INK)
        ax.grid(alpha=.15, color=INK)
        return ax

    def draw(self):
        self.fig.clear()
        title, description, equation, variables, reference = LESSONS[self.topic]
        self.fig.text(.04, .95, title, fontsize=18, weight='bold', color=INK, va='top')
        self.fig.text(.04, .88, textwrap.fill(description, 111), fontsize=11, color=INK, va='top', linespacing=1.5)
        self.fig.text(.5, .735, equation, fontsize=17, color=GOLD, ha='center')
        self.fig.text(.04, .65, textwrap.fill(variables, 115), fontsize=10, color=BLUE, va='top')

        self.fig.text(.04, .018, 'LEARNING EXAMPLE · No commands sent to the rover', fontsize=8, color=GREEN)
        ax = self.axes(); t = self.elapsed
        if self.topic == 0:
            points, parents, costs, rewired, sample = self.frames[min(len(self.frames)-1, int(t*10) % (len(self.frames)+35))]
            lines = [(points[i], points[p]) for i, p in enumerate(parents) if p >= 0]
            ax.add_collection(LineCollection(lines, colors=BLUE, linewidths=1, alpha=.8))
            if rewired: ax.add_collection(LineCollection([(points[i], points[parents[i]]) for i in rewired], colors=GOLD, linewidths=3))
            ax.add_patch(Rectangle((4, 2), 2, 5, color=RED, alpha=.7))
            ax.scatter(*sample, marker='x', color=GOLD, s=40)
            goal = np.array([9., 9.]); ax.scatter(*goal, marker='*', color=GREEN, s=150)
            valid = [i for i, p in enumerate(points) if np.linalg.norm(p-goal)<1.5 and edge_free(p, goal)]
            if valid:
                i = min(valid, key=lambda j: costs[j]+np.linalg.norm(points[j]-goal)); route = [goal]
                while i >= 0: route.append(points[i]); i = parents[i]
                route = np.array(route); ax.plot(route[:, 0], route[:, 1], color=GREEN, lw=3)
            ax.set(xlim=(0, 10), ylim=(0, 10)); ax.set_aspect('equal')
            ax.set_title(f'{len(points)} nodes · Blue: tree · Gold: sample/new connection · Green: path', color=INK, fontsize=10)
        elif self.topic == 1:
            rover = np.array([1., 2.]); goals = np.array([[4, 3], [8, 7], [2, 3]])
            observed = (True, True, t % 10 >= 5)
            chosen = min((i for i in range(3) if observed[i]), key=lambda i: np.linalg.norm(goals[i]-rover))
            ax.scatter(*rover, color=BLUE, s=100, marker='s')
            for i, p in enumerate(goals):
                ax.scatter(*p, color=GREEN if observed[i] else '#687a8a', marker='*', s=150)
                ax.text(p[0]+.2, p[1], f'G{i+1}: {np.linalg.norm(p-rover):.1f} m', color=INK)
            p = goals[chosen]; ax.plot([rover[0], p[0]], [rover[1], p[1]], color=GOLD, lw=3)
            ax.set(xlim=(0, 10), ylim=(0, 9)); ax.set_title('Example targets · This does not change the mission path', color=INK, fontsize=9)
        elif self.topic == 2:
            ld, length, alpha = 6., 4., .65*np.sin(t*.65)
            curvature = 2*np.sin(alpha)/ld
            d = np.linspace(0, 8, 150)
            x = (1-np.cos(curvature*d))/curvature if abs(curvature)>1e-6 else d*0
            z = np.sin(curvature*d)/curvature if abs(curvature)>1e-6 else d
            ax.plot(x, z, color=GREEN, lw=3); ax.scatter(ld*np.sin(alpha), ld*np.cos(alpha), color=GOLD, s=100)
            ax.arrow(0, 0, 0, 1.3, color=BLUE, head_width=.2); ax.set(xlim=(-6, 6), ylim=(0, 9))
            ax.set_title(f'Curvature {curvature:.2f} 1/m · requested steering {np.degrees(np.arctan(length*curvature)):.1f}° (before limits)', color=INK, fontsize=10)
        elif self.topic == 3:
            v = 2+1.8*np.sin(t*.6); speeds = np.linspace(0, 4, 120)
            ax.set(xlabel='Speed [m/s]', ylabel='Stopping distance [m]')
            ax.xaxis.label.set_color(INK); ax.yaxis.label.set_color(INK)
            ax.plot(speeds, speeds*.5+speeds**2/2+.25, color=BLUE)
            ax.axhline(5, color=RED, ls='--'); ax.scatter(v, v*.5+v*v/2+.25, color=GOLD, s=90)
            ax.set_title('a = 1 m/s² · Reaction time = 0.5 s · Red: 5 m checked range', color=INK, fontsize=10)
        elif self.topic == 4:
            origin = np.array([1., 1.]); angle = -.3+(t % 8)/8*1.6; end = origin+9*np.array([np.cos(angle), np.sin(angle)])
            if end[0] > 5:
                hit = origin+(end-origin)*4/(end[0]-1)
                if 0 <= hit[1] <= 6:
                    ax.plot([hit[0], end[0]], [hit[1], end[1]], '--', color='#687a8a'); end = hit
            ax.plot([origin[0], end[0]], [origin[1], end[1]], color=GOLD, lw=2)
            ax.plot([5, 5], [0, 6], color=RED, lw=6); ax.scatter(*end, color=GREEN, s=70)
            ax.set(xlim=(0, 11), ylim=(-1, 10)); ax.set_title('Red: wall · Green: first hit · Hidden ground stays unknown', color=INK, fontsize=10)
        elif self.topic == 5:
            h = .15+.7*(.5+.5*np.sin(t*.7)); allowed = h <= .53
            ax.plot([-2, 0, 0, 3], [0, 0, h, h], color=GREEN if allowed else RED, lw=4)
            ax.scatter([-1, 1], [0, h], color=BLUE, s=80); ax.axhline(.5, color=GOLD, ls='--')
            ax.set(xlim=(-2, 3), ylim=(-.1, 1), ylabel='Height [m]'); ax.yaxis.label.set_color(INK)
            ax.set_title(f'Δh = {h:.2f} m · example limit 0.50 m + tolerance 0.03 m · '+('passable' if allowed else 'blocked'), color=INK, fontsize=10)
        elif self.topic == 6:
            count = int(t*2) % 36; grid = np.zeros((6, 6)); grid.flat[:count] = 1
            if count > 20: grid[2, 2] = 2
            ax.imshow(grid, origin='lower', extent=(0, 6, 0, 6), cmap=matplotlib.colors.ListedColormap([PANEL, BLUE, GOLD]), vmin=0, vmax=2)
            ax.set_title('Blue: stored cells · Gold: only this cell changed after a new scan', color=INK, fontsize=10)
        elif self.topic == 7:
            times, compression = self.suspension; at = int(t/.02) % len(times)
            ax.plot(times, compression, color=BLUE); ax.scatter(times[at], compression[at], color=GOLD, s=80)
            ax.axhline(50*1.62/2200, color=GREEN, ls='--'); ax.set(xlabel='Time [s]', ylabel='Compression [m]')
            ax.xaxis.label.set_color(INK); ax.yaxis.label.set_color(INK)
            ax.set_title('Mass 50 kg · Gravity 1.62 m/s² · Green: balance kx = mg', color=INK, fontsize=10)
        elif self.topic == 8:
            request = np.array([1.5*np.cos(t*.65), 1.5*np.sin(t*.9)])
            applied = request/max(1, np.linalg.norm(request))
            ax.add_patch(Circle((0, 0), 1, fill=False, color=BLUE, lw=2))
            ax.arrow(0, 0, *request, color=RED, head_width=.08, length_includes_head=True)
            ax.arrow(0, 0, *applied, color=GREEN, head_width=.08, length_includes_head=True)
            ax.set(xlim=(-1.8, 1.8), ylim=(-1.8, 1.8), xlabel='Fx / (mu N)', ylabel='Fy / (mu N)'); ax.set_aspect('equal')
            ax.xaxis.label.set_color(INK); ax.yaxis.label.set_color(INK)
            ax.set_title('Red: requested force · Green: force limited by grip', color=INK, fontsize=10)
        elif self.topic == 9:
            beta = .7+.5*np.sin(t*.5); actual = .7+.5*np.sin((t-.5)*.5)
            sun = np.array([np.sin(beta), np.cos(beta)]); normal = np.array([np.sin(actual), np.cos(actual)])
            tangent = np.array([normal[1], -normal[0]])
            ax.plot([-tangent[0], tangent[0]], [-tangent[1], tangent[1]], color=BLUE, lw=9)
            ax.arrow(0, 0, *(1.7*sun), color=GOLD, head_width=.09); ax.arrow(0, 0, *(1.3*normal), color=GREEN, head_width=.09)
            ax.set(xlim=(-2.3, 2.3), ylim=(-1.3, 2.3), xlabel='Horizontal slice [relative units]', ylabel='Vertical [relative units]'); ax.set_aspect('equal')
            ax.xaxis.label.set_color(INK); ax.yaxis.label.set_color(INK)
            ax.set_title(f'Example side view · Gold: Sun · Green: panel normal · n·s = {np.dot(normal,sun):.3f}', color=INK, fontsize=10)
        elif self.topic == 11:
            a=.5+.4*np.sin(t*.6); b=1+.6*np.cos(t*.4)
            x=np.array([0,np.cos(a),np.cos(a)+np.cos(a+b)])
            y=np.array([0,np.sin(a),np.sin(a)+np.sin(a+b)])
            ax.plot(x,y,'o-',color=GREEN,lw=7,markersize=10)
            ax.set(xlim=(-2.2,2.2),ylim=(-.3,2.2),xlabel='x [m]',ylabel='y [m]');ax.set_aspect('equal')
        elif self.topic == 12:
            u=np.linspace(0,1,160);q=3*u*u-2*u*u*u
            ax.plot(u,q,label='Position',color=GREEN);ax.plot(u,6*u-6*u*u,label='Speed',color=GOLD);ax.plot(u,6-12*u,label='Acceleration',color=BLUE)
            ax.axvline((t/6)%1,color=RED);ax.legend();ax.set(xlabel='Time from 0 to 1 (u)',ylabel='Relative value')
        else:
            q = (1, 2, 4)[int(t/3) % 3]; source = np.zeros((16, 16)); source[7:9, 7:9] = 1
            reduced = source.reshape(16//q, q, 16//q, q).max(axis=(1, 3))
            ax.imshow(reduced, origin='lower', extent=(0, 16, 0, 16), vmin=0, vmax=1, cmap=matplotlib.colors.ListedColormap([BLUE, RED]), interpolation='nearest')
            ax.set_xticks(np.arange(0, 17, q)); ax.set_yticks(np.arange(0, 17, q)); ax.grid(color=BG, alpha=.6)
            ax.set_title(f'q = {q} · {(16//q)**2} visible cells · all 256 original cells are still stored', color=INK, fontsize=10)

    def close(self):
        plt.close(self.fig)
