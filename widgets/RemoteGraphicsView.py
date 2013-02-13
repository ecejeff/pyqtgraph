from pyqtgraph.Qt import QtGui, QtCore
import pyqtgraph.multiprocess as mp
import pyqtgraph as pg
from .GraphicsView import GraphicsView
import numpy as np
import mmap, tempfile, ctypes, atexit, sys, random

__all__ = ['RemoteGraphicsView']

class RemoteGraphicsView(QtGui.QWidget):
    """
    Replacement for GraphicsView that does all scene management and rendering on a remote process,
    while displaying on the local widget.
    
    GraphicsItems must be created by proxy to the remote process.
    
    """
    def __init__(self, parent=None, *args, **kwds):
        self._img = None
        self._imgReq = None
        self._sizeHint = (640,480)  ## no clue why this is needed, but it seems to be the default sizeHint for GraphicsView.
                                    ## without it, the widget will not compete for space against another GraphicsView.
        QtGui.QWidget.__init__(self)
        self._proc = mp.QtProcess()
        self.pg = self._proc._import('pyqtgraph')
        self.pg.setConfigOptions(**self.pg.CONFIG_OPTIONS)
        rpgRemote = self._proc._import('pyqtgraph.widgets.RemoteGraphicsView')
        self._view = rpgRemote.Renderer(*args, **kwds)
        self._view._setProxyOptions(deferGetattr=True)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy(self._view.focusPolicy()))
        self.setSizePolicy(QtGui.QSizePolicy.Expanding, QtGui.QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.shm = None
        shmFileName = self._view.shmFileName()
        if sys.platform.startswith('win'):
            self.shmtag = shmFileName
        else:
            self.shmFile = open(shmFileName, 'r')
        
        self._view.sceneRendered.connect(mp.proxy(self.remoteSceneChanged)) #, callSync='off'))
                                                                            ## Note: we need synchronous signals
                                                                            ## even though there is no return value--
                                                                            ## this informs the renderer that it is 
                                                                            ## safe to begin rendering again. 
        
        for method in ['scene', 'setCentralItem']:
            setattr(self, method, getattr(self._view, method))
        
    def resizeEvent(self, ev):
        ret = QtGui.QWidget.resizeEvent(self, ev)
        self._view.resize(self.size(), _callSync='off')
        return ret
        
    def sizeHint(self):
        return QtCore.QSize(*self._sizeHint)
        
    def remoteSceneChanged(self, data):
        w, h, size, newfile = data
        #self._sizeHint = (whint, hhint)
        if self.shm is None or self.shm.size != size:
            if self.shm is not None:
                self.shm.close()
            if sys.platform.startswith('win'):
                self.shmtag = newfile   ## on windows, we create a new tag for every resize
                self.shm = mmap.mmap(-1, size, self.shmtag) ## can't use tmpfile on windows because the file can only be opened once.
            else:
                self.shm = mmap.mmap(self.shmFile.fileno(), size, mmap.MAP_SHARED, mmap.PROT_READ)
        self.shm.seek(0)
        self._img = QtGui.QImage(self.shm.read(w*h*4), w, h, QtGui.QImage.Format_ARGB32)
        self.update()
        
    def paintEvent(self, ev):
        if self._img is None:
            return
        p = QtGui.QPainter(self)
        p.drawImage(self.rect(), self._img, QtCore.QRect(0, 0, self._img.width(), self._img.height()))
        p.end()
        
    def mousePressEvent(self, ev):
        self._view.mousePressEvent(ev.type(), ev.pos(), ev.globalPos(), ev.button(), int(ev.buttons()), int(ev.modifiers()), _callSync='off')
        ev.accept()
        return QtGui.QWidget.mousePressEvent(self, ev)

    def mouseReleaseEvent(self, ev):
        self._view.mouseReleaseEvent(ev.type(), ev.pos(), ev.globalPos(), ev.button(), int(ev.buttons()), int(ev.modifiers()), _callSync='off')
        ev.accept()
        return QtGui.QWidget.mouseReleaseEvent(self, ev)

    def mouseMoveEvent(self, ev):
        self._view.mouseMoveEvent(ev.type(), ev.pos(), ev.globalPos(), ev.button(), int(ev.buttons()), int(ev.modifiers()), _callSync='off')
        ev.accept()
        return QtGui.QWidget.mouseMoveEvent(self, ev)
        
    def wheelEvent(self, ev):
        self._view.wheelEvent(ev.pos(), ev.globalPos(), ev.delta(), int(ev.buttons()), int(ev.modifiers()), ev.orientation(), _callSync='off')
        ev.accept()
        return QtGui.QWidget.wheelEvent(self, ev)
    
    def keyEvent(self, ev):
        if self._view.keyEvent(ev.type(), int(ev.modifiers()), text, autorep, count):
            ev.accept()
        return QtGui.QWidget.keyEvent(self, ev)
        
    def enterEvent(self, ev):
        self._view.enterEvent(ev.type(), _callSync='off')
        return QtGui.QWidget.enterEvent(self, ev)
        
    def leaveEvent(self, ev):
        self._view.leaveEvent(ev.type(), _callSync='off')
        return QtGui.QWidget.leaveEvent(self, ev)
        
    def remoteProcess(self):
        """Return the remote process handle. (see multiprocess.remoteproxy.RemoteEventHandler)"""
        return self._proc
    
class Renderer(GraphicsView):
    
    sceneRendered = QtCore.Signal(object)
    
    def __init__(self, *args, **kwds):
        ## Create shared memory for rendered image
        if sys.platform.startswith('win'):
            self.shmtag = "pyqtgraph_shmem_" + ''.join([chr((random.getrandbits(20)%25) + 97) for i in range(20)])
            self.shm = mmap.mmap(-1, mmap.PAGESIZE, self.shmtag) # use anonymous mmap on windows
        else:
            self.shmFile = tempfile.NamedTemporaryFile(prefix='pyqtgraph_shmem_')
            self.shmFile.write('\x00' * mmap.PAGESIZE)
            fd = self.shmFile.fileno()
            self.shm = mmap.mmap(fd, mmap.PAGESIZE, mmap.MAP_SHARED, mmap.PROT_WRITE)
        atexit.register(self.close)
        
        GraphicsView.__init__(self, *args, **kwds)
        self.scene().changed.connect(self.update)
        self.img = None
        self.renderTimer = QtCore.QTimer()
        self.renderTimer.timeout.connect(self.renderView)
        self.renderTimer.start(16)
        
    def close(self):
        self.shm.close()
        if sys.platform.startswith('win'):
            self.shmFile.close()
        
    def shmFileName(self):
        if sys.platform.startswith('win'):
            return self.shmtag
        else:
            return self.shmFile.name
        
    def update(self):
        self.img = None
        return GraphicsView.update(self)
        
    def resize(self, size):
        oldSize = self.size()
        GraphicsView.resize(self, size)
        self.resizeEvent(QtGui.QResizeEvent(size, oldSize))
        self.update()
        
    def renderView(self):
        if self.img is None:
            ## make sure shm is large enough and get its address
            if self.width() == 0 or self.height() == 0:
                return
            size = self.width() * self.height() * 4
            if size > self.shm.size():
                if sys.platform.startswith('win'):
                    ## windows says "WindowsError: [Error 87] the parameter is incorrect" if we try to resize the mmap
                    self.shm.close()
                    ## it also says (sometimes) 'access is denied' if we try to reuse the tag.
                    self.shmtag = "pyqtgraph_shmem_" + ''.join([chr((random.getrandbits(20)%25) + 97) for i in range(20)])
                    self.shm = mmap.mmap(-1, size, self.shmtag)
                else:
                    self.shm.resize(size)
            address = ctypes.addressof(ctypes.c_char.from_buffer(self.shm, 0))
            
            ## render the scene directly to shared memory
            self.img = QtGui.QImage(address, self.width(), self.height(), QtGui.QImage.Format_ARGB32)
            self.img.fill(0xffffffff)
            p = QtGui.QPainter(self.img)
            self.render(p, self.viewRect(), self.rect())
            p.end()
            self.sceneRendered.emit((self.width(), self.height(), self.shm.size(), self.shmFileName()))

    def mousePressEvent(self, typ, pos, gpos, btn, btns, mods):
        typ = QtCore.QEvent.Type(typ)
        btns = QtCore.Qt.MouseButtons(btns)
        mods = QtCore.Qt.KeyboardModifiers(mods)
        return GraphicsView.mousePressEvent(self, QtGui.QMouseEvent(typ, pos, gpos, btn, btns, mods))

    def mouseMoveEvent(self, typ, pos, gpos, btn, btns, mods):
        typ = QtCore.QEvent.Type(typ)
        btns = QtCore.Qt.MouseButtons(btns)
        mods = QtCore.Qt.KeyboardModifiers(mods)
        return GraphicsView.mouseMoveEvent(self, QtGui.QMouseEvent(typ, pos, gpos, btn, btns, mods))

    def mouseReleaseEvent(self, typ, pos, gpos, btn, btns, mods):
        typ = QtCore.QEvent.Type(typ)
        btns = QtCore.Qt.MouseButtons(btns)
        mods = QtCore.Qt.KeyboardModifiers(mods)
        return GraphicsView.mouseReleaseEvent(self, QtGui.QMouseEvent(typ, pos, gpos, btn, btns, mods))

    def wheelEvent(self, pos, gpos, d, btns, mods, ori):
        btns = QtCore.Qt.MouseButtons(btns)
        mods = QtCore.Qt.KeyboardModifiers(mods)
        return GraphicsView.wheelEvent(self, QtGui.QWheelEvent(pos, gpos, d, btns, mods, ori))

    def keyEvent(self, typ, mods, text, autorep, count):
        typ = QtCore.QEvent.Type(typ)
        mods = QtCore.Qt.KeyboardModifiers(mods)
        GraphicsView.keyEvent(self, QtGui.QKeyEvent(typ, mods, text, autorep, count))
        return ev.accepted()
        
    def enterEvent(self, typ):
        ev = QtCore.QEvent(QtCore.QEvent.Type(typ))
        return GraphicsView.enterEvent(self, ev)

    def leaveEvent(self, typ):
        ev = QtCore.QEvent(QtCore.QEvent.Type(typ))
        return GraphicsView.leaveEvent(self, ev)


        
        