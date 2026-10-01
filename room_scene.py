"""Interactive council chamber. Artwork is decorative; chat state stays in RoomApp."""
import tkinter as tk
import tkinter.font as tkfont
from PIL import Image, ImageTk
from config import RESOURCE_DIR


ROOT = RESOURCE_DIR
BG = '#070d18'
PANEL = '#0b1726'
TEXT = '#e1edfa'
MUTED = '#91a7bc'
BLUE = '#57cfff'

# Visor centres in council-chamber-v1.png, from the back of the table forwards.
SEATS = (
    (.391, .222, 'left', 0), (.291, .281, 'left', 1),
    (.213, .349, 'left', 2), (.610, .222, 'right', 0),
    (.707, .281, 'right', 1), (.790, .349, 'right', 2),
)


class CouncilScene(tk.Canvas):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG, highlightthickness=0)
        self.app = app
        self.targets = []
        self._scene_key = None
        self._scene_photo = None
        self._icon_cache = {}
        self._resize_job = None
        self._font_cache = {}
        try:
            with Image.open(ROOT/'assets'/'room'/'council-chamber-v1.png') as im:
                self.artwork = im.convert('RGB')
        except OSError:
            self.artwork = None
        self.bind('<Configure>', self._resize)
        self.bind('<Button-1>', self._click)
        self.bind('<Motion>', self._motion)
        self.bind('<Leave>', lambda _: self.configure(cursor=''))

    def _resize(self, _event):
        if self._resize_job is not None:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(60, self._render_after_resize)

    def _render_after_resize(self):
        self._resize_job = None
        self.render()

    def _target(self, box, callback):
        self.targets.append((box, callback))

    def _hit(self, x, y):
        for (x1, y1, x2, y2), callback in reversed(self.targets):
            if x1 <= x <= x2 and y1 <= y <= y2:
                return callback
        if hasattr(self, '_public_ellipse'):
            cx, cy, rx, ry = self._public_ellipse
            if ((x-cx)/rx)**2+((y-cy)/ry)**2 <= 1:
                return self.app.public_all
        return None

    def _click(self, event):
        callback = self._hit(event.x, event.y)
        if callback:
            callback()

    def _motion(self, event):
        self.configure(cursor='hand2' if self._hit(event.x, event.y) else '')

    def _panel(self, x, y, width, height, edge, tag):
        # Chamfered corners keep the scene's speech panels distinct from tables.
        r = 9
        self.create_polygon(x+r, y, x+width-r, y, x+width, y+r,
                            x+width, y+height-r, x+width-r, y+height,
                            x+r, y+height, x, y+height-r, x, y+r,
                            fill=PANEL, outline=edge, width=1, tags=tag)

    def _font(self, size, bold=False):
        key = (size, bold)
        if key not in self._font_cache:
            self._font_cache[key] = tkfont.Font(
                family='Segoe UI', size=size, weight='bold' if bold else 'normal')
        return self._font_cache[key]

    def _seat_name(self, fid, width, size):
        prefix = f'{self.app.order.index(fid)+1}. '
        label = self.app.labels[fid]
        suffix = ' (LLM)' if self.app.specs[fid]['provider']=='local' else ''
        name = label.removesuffix(suffix) if suffix else label
        font = self._font(size, True)
        if font.measure(prefix+name+suffix) <= width:
            return prefix+name+suffix
        while name and font.measure(prefix+name+'…'+suffix) > width:
            name = name[:-1]
        return prefix+name.rstrip()+'…'+suffix

    def _fit_text(self, text, width, lines, size=10):
        """Bound excerpts by measured pixel width, including unbroken model text."""
        font = self._font(size)
        words = str(text).split()
        result = []
        current = ''
        for word in words:
            while word and font.measure(word) > width:
                if current:
                    result.append(current)
                    current = ''
                split = max(1, len(word)//2)
                while split > 1 and font.measure(word[:split]) > width:
                    split -= 1
                while split < len(word) and font.measure(word[:split+1]) <= width:
                    split += 1
                result.append(word[:split])
                word = word[split:]
            candidate = (current+' '+word).strip()
            if current and font.measure(candidate) > width:
                result.append(current)
                current = word
            else:
                current = candidate
            if len(result) >= lines:
                break
        if current:
            result.append(current)
        clipped = len(result) > lines or ' '.join(result).strip() != ' '.join(words)
        result = result[:lines]
        if clipped and result:
            tail = result[-1]
            while tail and font.measure(tail+'…') > width:
                tail = tail[:-1]
            result[-1] = tail.rstrip()+'…'
        return '\n'.join(result)

    def _background(self, width, height, compact):
        if self.artwork is None:
            self.create_text(width/2, height*.14, text='Council artwork unavailable',
                             fill=MUTED, font=self._font(12))
            return 0, 0, width, height
        iw, ih = self.artwork.size
        # Preserve every seat in the narrower view beside the transcript.
        scale = min(width/iw, height/ih) if compact else max(width/iw, height/ih)
        sw, sh = max(1, round(iw*scale)), max(1, round(ih*scale))
        ox, oy = (width-sw)/2, (height-sh)/2
        key = (width, height, compact)
        if key != self._scene_key:
            fitted = self.artwork.resize((sw, sh), Image.Resampling.LANCZOS)
            # Keep the Tk image viewport-sized when filling a wide scene.
            left, top = max(0, (sw-width)//2), max(0, (sh-height)//2)
            if not compact:
                fitted = fitted.crop((left, top, left+width, top+height))
            self._scene_photo = ImageTk.PhotoImage(fitted, master=self)
            self._scene_key = key
        self.create_image(width/2, height/2, image=self._scene_photo)
        return ox, oy, sw, sh

    def _icon(self, fid, size):
        original = self.app.icons.get(fid)
        if original is None:
            return None
        key = (fid, str(original), size)
        if key not in self._icon_cache:
            # Read the already-loaded icon without altering the original asset.
            source = ImageTk.getimage(original) if isinstance(original, ImageTk.PhotoImage) else None
            if source is None:
                import io
                # PhotoImage.data() was added after Python 3.12; the underlying
                # Tk command is available on every supported Python version.
                data = original.tk.call(str(original), 'data', '-format', 'png')
                if isinstance(data, str):data = data.encode('latin1')
                source = Image.open(io.BytesIO(data))
            self._icon_cache = {k:v for k,v in self._icon_cache.items() if k[0] != fid}
            self._icon_cache[key] = ImageTk.PhotoImage(
                source.convert('RGBA').resize((size, size), Image.Resampling.LANCZOS), master=self)
        return self._icon_cache[key]

    def render(self):
        width, height = self.winfo_width(), self.winfo_height()
        if width < 20 or height < 20:
            return
        self.delete('all')
        self.targets = []
        app = self.app
        compact = width < 1100
        ox, oy, sw, sh = self._background(width, height, compact)
        pt = lambda x,y: (ox+x*sw, oy+y*sh)
        self._public_ellipse = (*pt(.5,.51),sw*.26,sh*.17)

        # Table and host remain public-channel targets, even while a DM is open.
        tx, ty = pt(.5, .447)
        table_width = min(270, sw*.24)
        self._panel(tx-table_width/2, ty-22, table_width, 67,
                    BLUE if app.channel=='public' else '#315168', 'public_table')
        self.create_text(tx, ty-6, text='PUBLIC ROOM', fill=BLUE,
                         font=self._font(9, True), tags='public_table')
        topic = self._fit_text(app.topic.get() or 'Open conversation', table_width-24, 1, 11)
        self.create_text(tx, ty+14, text=topic, fill=TEXT,
                         font=self._font(11, True), tags='public_table')
        self.create_text(tx, ty+33, text='Click to talk to everyone', fill=MUTED,
                         font=self._font(8), tags='public_table')
        self._target((tx-table_width/2,ty-22,tx+table_width/2,ty+45),app.public_all)
        # The unobstructed inner table surface is also clickable.
        self._target((*pt(.36,.38), *pt(.64,.53)), app.public_all)

        hx, hy = pt(.5, .755)
        self._panel(hx-75, hy-18, 150, 53, BLUE, 'host')
        self.create_text(hx, hy-2, text='YOU', fill=TEXT,
                         font=self._font(13,True), tags='host')
        host = app.host.get().strip()
        self.create_text(hx, hy+20, text=host if host.lower()!='you' else 'Human host',
                         fill=BLUE, font=self._font(9), tags='host')
        self._target((*pt(.40,.56), *pt(.60,.98)), app.public_all)

        latest = {}
        for entry in app.conversation.visible('public'):
            if entry['speaker'] in app.fids:
                latest[entry['speaker']] = entry
        self.create_text(18, 15, text='THE COUNCIL CHAMBER', anchor='nw', fill=MUTED,
                         font=self._font(8,True))
        for index, fid in enumerate(app.fids):
            sx, sy, side, row = SEATS[index]
            x, y = pt(sx, sy)
            active = app.active == fid
            selected = app.channel == fid
            color = app.colors[fid]
            radius = max(17, min(25, sw*.016))
            tag = 'seat_'+fid
            if active or selected:
                for extra, edge in ((9,'#183d54'),(5,'#2b617a'),(1,color)):
                    self.create_oval(x-radius-extra,y-radius-extra,x+radius+extra,y+radius+extra,
                                     outline=edge,width=2,tags=tag)
            self.create_oval(x-radius,y-radius,x+radius,y+radius,
                             fill='#07131e',outline=color if active or selected else '#36536a',
                             width=2,tags=tag)
            icon = self._icon(fid, round(radius*1.35))
            if icon is not None:
                self.create_image(x,y,image=icon,tags=tag)
            else:
                self.create_text(x,y,text=app.labels[fid][:2],fill=color,
                                 font=self._font(11,True),tags=tag)
            callback = lambda f=fid: app.open_seat(f)
            # Head, torso and the exposed outer portion of each chair.
            body_width = sw*(.048 if row < 2 else .058)
            body_end = y+sh*(.14 if row < 2 else .39)
            self._target((x-body_width,y-radius-8,x+body_width,body_end), callback)

            state = ('THINKING…' if active else 'PRIVATE OPEN' if selected else
                     'INVITED' if app.select[fid].get() else 'NOT INVITED')
            if compact:
                name = self._seat_name(fid, min(165, sw*.20), 9)
                self.create_text(x,y+radius+12,text=name,fill=color,
                                 font=self._font(9,True),tags=tag)
                self._target((x-70,y-radius-8,x+70,y+radius+24),callback)
                if active:
                    self.create_text(x,y+radius+28,text=state,fill=TEXT,font=self._font(8),tags=tag)
                continue

            card_width = min(260, max(185,width*.18))
            card_height = min(154, max(116,height*.23))
            cx = 18 if side=='left' else width-card_width-18
            cy = 43+row*(height-70-card_height)/2
            edge = color if active or selected else '#284259'
            line_x = cx+card_width if side=='left' else cx
            self.create_line(line_x,cy+32,x,y,fill=edge,width=1,tags=tag)
            self._panel(cx,cy,card_width,card_height,edge,tag)
            self.create_text(cx+13,cy+14,text=self._seat_name(fid,card_width-26,10),
                             anchor='nw',fill=color,font=self._font(10,True),tags=tag)
            self.create_text(cx+13,cy+37,text=state+'  ·  PRIVATE CHAT ↗',
                             anchor='nw',fill=MUTED,font=self._font(7),tags=tag)
            entry = latest.get(fid)
            snippet = entry['text'] if entry else 'Ready when you are. Send a message to begin.'
            if active:
                snippet = ('Replying privately to you. Public replies stay here.'
                           if app.work_channel!='public' else 'Considering the conversation…')
            size = 10 if card_width >= 225 else 9
            lines = max(2, int((card_height-70)/self._font(size).metrics('linespace')))
            self.create_text(cx+13,cy+61,text=self._fit_text(snippet,card_width-26,lines,size),
                             anchor='nw',fill='#ffaaaa' if entry and entry.get('error') else TEXT,
                             font=self._font(size),tags=('public_excerpt',tag))
            self._target((cx,cy,cx+card_width,cy+card_height),callback)

        self.create_text(width/2,height-13,
                         text='Click a model to chat privately  ·  Click the table or your chair to address everyone',
                         fill=MUTED,font=self._font(8))
