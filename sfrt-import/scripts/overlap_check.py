"""用格网索引快速算出 SFRT 各运行线之间的几何重叠（判断共线区段）。"""
import math, sys, itertools, collections
sys.path.insert(0,'.')
import sfrt_network as SN

def build():
    d=SN.load(); lines=SN.load_lines()
    sf_ids={l['id'] for l in lines if l['systemId']=='SFRT'}
    chains,nodes,scope=SN.build_chains(d,sf_ids)
    by=collections.defaultdict(list)
    for c in chains: by[c.line_id].append(c)
    return by

CELL=16.0
def index_segs(by,lid):
    idx=collections.defaultdict(list)
    for pl in by[lid]:
        for q,r in zip(pl.coords,pl.coords[1:]):
            x0,x1=sorted((q[0],r[0])); y0,y1=sorted((q[1],r[1]))
            for cx in range(int(x0//CELL),int(x1//CELL)+1):
                for cy in range(int(y0//CELL),int(y1//CELL)+1):
                    idx[(cx,cy)].append((q,r))
    return idx

def dist_pt_seg(p,q,r):
    qx,qy=q[0],q[1]; rx,ry=r[0],r[1]; px,py=p[0],p[1]
    dx,dy=rx-qx,ry-qy; L2=dx*dx+dy*dy
    if L2<=1e-12: return math.hypot(px-qx,py-qy)
    t=max(0.0,min(1.0,((px-qx)*dx+(py-qy)*dy)/L2))
    return math.hypot(px-(qx+t*dx),py-(qy+t*dy))

def overlap(by,a,b,tol=1.5):
    idx=index_segs(by,b)
    sh=0.0; tot=0.0
    for pl in by[a]:
        for q,r in zip(pl.coords,pl.coords[1:]):
            d=math.hypot(r[0]-q[0],r[1]-q[1])
            n=max(2,int(d/6)+1); L=d/n
            for i in range(n):
                t=(i+0.5)/n
                p=(q[0]+(r[0]-q[0])*t, q[1]+(r[1]-q[1])*t)
                tot+=L
                cx,cy=int(p[0]//CELL),int(p[1]//CELL)
                best=1e9
                for dx in (-1,0,1):
                    for dy in (-1,0,1):
                        for s in idx.get((cx+dx,cy+dy),()):
                            dd=dist_pt_seg(p,s[0],s[1])
                            if dd<best: best=dd
                            if best<tol: break
                        if best<tol: break
                    if best<tol: break
                if best<tol: sh+=L
    return sh,tot

if __name__=='__main__':
    by=build(); ids=sorted(by)
    res=[]
    for a,b in itertools.combinations(ids,2):
        sh,tot=overlap(by,a,b)
        if sh>200: res.append((a,b,sh,tot))
    print('=== 重叠 >= 0.2 km（容差 1.5 格）===')
    for a,b,sh,tot in sorted(res,key=lambda r:-r[2]):
        print(f"  {a:7s} & {b:7s}: {sh/1000:6.2f} km  (占 {a} 的 {sh/tot*100:4.0f}%)")
