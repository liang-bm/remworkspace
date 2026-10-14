"""X11/XWayland child-window rounding using the existing checked Shape bridge."""
import math
from functools import lru_cache
from .x11_shape import _ShapeConnection,_Rectangle

@lru_cache(maxsize=256)
def _rounded_rectangles(width,height,radius):
    radius=min(radius,width//2,height//2)
    values=[]
    for y in range(height):
        edge=min(y,height-1-y)
        inset=math.ceil(radius-math.sqrt(max(0,radius*radius-(radius-edge-.5)**2))) if edge<radius else 0
        run=(inset,width-inset*2)
        if values and values[-1][0]==run[0] and values[-1][2]==run[1]:
            previous=values[-1];values[-1]=(previous[0],previous[1],previous[2],previous[3]+1)
        else:values.append((inset,y,run[1],1))
    return tuple(values)

class WindowMasks:
    def __init__(self,root):self.native=_ShapeConnection(root.winfo_screen())
    def apply(self,widget,width,height,radius):
        if min(width,height)<2 or max(width,height)>32767:return False
        values=_rounded_rectangles(width,height,radius)
        rectangles=(_Rectangle*len(values))(*(_Rectangle(*value) for value in values))
        self.native.apply(widget.winfo_id(),rectangles);return True
    def close(self):self.native.close()
