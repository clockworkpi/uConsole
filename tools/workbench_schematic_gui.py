"""Tk functional-map view. Selection only navigates; it never actuates hardware."""
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from workbench_schematic import COMPONENTS, BY_ID, LINKS, Observations
from workbench_schematic_live import Collector
from workbench_schematic_trace import Recording, Playback
from workbench_layout import WrappingToolbar
from workbench_help import Tooltip


COLORS = {'unknown': '#475569', 'present': '#2563eb', 'configured': '#0369a1',
          'active': '#15803d', 'fault': '#b91c1c', 'disconnected': '#64748b',
          'stale': '#92400e', 'off': '#475569', 'disabled': '#64748b', 'unavailable': '#92400e',
          'waiting': '#475569'}


def presentation(observation, component, *, running, replay=False):
    """UI vocabulary does not rewrite retained observation evidence."""
    value = dict(observation)
    if not running and not replay:
        value.update(state='off', detail='Emulator is off. Press Start in Workbench. '
                     'SSH targets do not supply live schematic telemetry.', source='', time=None)
    elif value['state'] == 'unknown':
        value['state'] = 'unavailable' if value['source'] else 'waiting'
        if not value['source']:
            value['detail'] = 'Waiting for the first observation.' if not replay else 'No observation in this recording yet.'
    elif value['state'] == 'disconnected' and value['source'] == 'QOM inventory':
        value['state'] = 'disabled'
        value['detail'] += ' Not included in this emulator configuration.'
    guidance = {
        'keyboard': 'To enable the composite keyboard, shut down and choose Setup → Emulated hardware → Keyboard: composite, then Finish. Generic input does not expose composite keyboard telemetry.',
        'audio': 'To enable audio, shut down and choose a non-none Audio surrogate in Setup, then Finish. For an already enabled device, use Audio controls → Connect.',
        'modem': 'To enable the modem, shut down and choose Modem: composite in Setup, then Finish. For an already enabled device, use Modem controls → Connect USB.',
    }
    if value['state'] in ('disabled', 'disconnected'):
        value['detail'] += '\n\n' + guidance.get(component, 'No interactive connection control is available for this component.')
    return value


class Schematic:
    def __init__(self, parent, navigate, runtime=lambda: None, source_root=None, configure=None):
        self.navigate = navigate
        if source_root is None:
            from uconsole_emulator import ROOT
            source_root = ROOT
        self.source_root = source_root
        self.runtime = runtime
        self.collector = Collector()
        self.observations = Observations()
        self.recording = None
        self.recording_active = False
        self.playback = None
        self.playback_start = None
        self.view_time = None
        self.window = tk.Toplevel(parent)
        self.window.title('uConsole — Live functional schematic')
        self.window.geometry('1150x720')
        self.window.minsize(760, 500)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.scale = 1.
        self.fitted = True
        self.selected = None
        self.panning = False
        self.timer = None
        self.mode = tk.StringVar(value='LIVE • no owned emulator attached')
        ttk.Label(self.window, textvariable=self.mode, padding=8).pack(fill='x')
        ttk.Label(self.window, text='Functional relationships, not electrical simulation. '
                  'Blue = present/configured · Green = observed activity · Amber = stale/unavailable · Gray = off/disabled/waiting',
                  wraplength=1050).pack(fill='x', padx=8)
        toolbar = WrappingToolbar(self.window, padding=8)
        toolbar.pack(fill='x')
        if configure is not None:
            ttk.Button(toolbar, text='Configure devices…', command=configure).pack(side='left', padx=4)
        ttk.Button(toolbar, text='Zoom +', command=lambda: self.zoom(1.2)).pack(side='left')
        ttk.Button(toolbar, text='Zoom −', command=lambda: self.zoom(1 / 1.2)).pack(side='left')
        ttk.Button(toolbar, text='Reset view', command=self.reset).pack(side='left')
        ttk.Label(toolbar, text='Drag background to pan. Select a component to inspect its code.').pack(side='left', padx=10)
        tracebar = WrappingToolbar(self.window, padding=8)
        tracebar.pack(fill='x')
        ttk.Button(tracebar, text='Record observations', command=self.record).pack(side='left')
        ttk.Button(tracebar, text='Stop recording', command=self.stop_record).pack(side='left')
        ttk.Button(tracebar, text='Save recording…', command=self.save_record).pack(side='left')
        ttk.Button(tracebar, text='Discard recording', command=self.discard_record).pack(side='left')
        ttk.Button(tracebar, text='Replay recording…', command=self.load_replay).pack(side='left')
        ttk.Button(tracebar, text='Return to live', command=self.live).pack(side='left')
        self.trace_status = tk.StringVar(value='Replay only displays evidence; it never sends input or changes hardware.')
        ttk.Label(self.window, textvariable=self.trace_status, padding=8, wraplength=1000).pack(fill='x')
        panes = ttk.Panedwindow(self.window, orient='horizontal')
        panes.pack(fill='both', expand=True, padx=8, pady=8)
        left = ttk.Frame(panes)
        panes.add(left, weight=1)
        self.search = tk.StringVar()
        ttk.Label(left, text='Find component').pack(anchor='w')
        search = ttk.Entry(left, textvariable=self.search)
        search.pack(fill='x')
        self.listing = tk.Listbox(left, exportselection=False, height=8)
        self.listing.pack(fill='x', pady=6)
        self.listing.bind('<<ListboxSelect>>', self.list_select)
        self.search.trace_add('write', self.filter)
        self.source = ttk.Combobox(left, state='readonly')
        self.source.pack(fill='x')
        ttk.Button(left, text='Open source in Host editor', command=self.open_source).pack(fill='x', pady=6)
        ttk.Button(left, text='Open schematic sheet…', command=self.open_sheet).pack(fill='x', pady=6)
        self.detail = ScrolledText(left, width=30, height=8, wrap='word', state='disabled')
        self.detail.pack(fill='both', expand=True)
        self.detail_text = ''
        self.canvas = tk.Canvas(panes, background='#0f172a', highlightthickness=0,
                                width=850, height=560)
        panes.add(self.canvas, weight=4)
        self.canvas.bind('<ButtonPress-1>', self.press)
        self.canvas.bind('<B1-Motion>', self.drag)
        self.canvas.bind('<Control-MouseWheel>', lambda event: self.zoom(1.1 if event.delta > 0 else 1 / 1.1))
        self.canvas.bind('<Button-4>', lambda event: self.zoom(1.1))
        self.canvas.bind('<Button-5>', lambda event: self.zoom(1 / 1.1))
        self.hover_tip = Tooltip(self.canvas, 'Select a component to inspect its implementation.')
        self.hover_component = None
        self.canvas.bind('<Motion>', self.hover)
        self.canvas.bind('<Leave>', self.leave, add='+')
        self.canvas.bind('<Configure>', self.fit)
        self.window.bind('<Destroy>', self.destroyed)
        self.filter()
        self.draw()
        self.tick()

    def filter(self, *unused):
        needle = self.search.get().casefold()
        self.visible = [c for c in COMPONENTS if needle in (c.label + ' ' + c.hardware).casefold()]
        self.listing.delete(0, 'end')
        for component in self.visible:
            self.listing.insert('end', component.label)

    def list_select(self, event=None):
        indexes = self.listing.curselection()
        if indexes:
            self.select(self.visible[indexes[0]].id)

    def select(self, component):
        self.selected = component
        item = BY_ID[component]
        self.source.configure(values=[link.label for link in item.sources])
        self.source.current(0)
        self.refresh()
        self.open_source()

    def open_source(self):
        if self.selected is not None and self.source.current() >= 0:
            self.navigate(BY_ID[self.selected].sources[self.source.current()])

    def open_sheet(self):
        if self.selected is None:
            raise ValueError('Select a component first')
        from workbench_schematic_sheet import Sheet
        return Sheet(self.window, self.source_root, BY_ID[self.selected], self.navigate)

    def press(self, event):
        self.panning = False
        current = self.canvas.find_withtag('current')
        if current:
            tags = self.canvas.gettags(current[0])
            for tag in tags:
                if tag.startswith('component:'):
                    self.select(tag.split(':', 1)[1])
                    return
        self.canvas.scan_mark(event.x, event.y)
        self.panning = True

    def hover(self, event):
        component = None
        for item in self.canvas.find_withtag('current'):
            for tag in self.canvas.gettags(item):
                if tag.startswith('component:'):
                    component = tag.split(':', 1)[1]
        if component != self.hover_component:
            self.hover_tip.hide()
            self.hover_component = component
            if component is not None:
                item = BY_ID[component]
                self.hover_tip.text = f'{item.label}\n{item.fidelity}\nClick to inspect {item.sources[0].label}.'
                self.hover_tip.schedule()

    def leave(self, event=None):
        self.hover_component = None

    def drag(self, event):
        if self.panning:
            self.canvas.scan_dragto(event.x, event.y, gain=1)

    def zoom(self, factor):
        self.fitted = False
        self.scale_to(min(2.5, max(.5, self.scale * factor)))

    def scale_to(self, target):
        self.canvas.scale('all', 0, 0, target / self.scale, target / self.scale)
        self.scale = target
        for label in self.labels.values():
            self.canvas.itemconfigure(label, width=150 * target, font=('TkDefaultFont', max(7, round(11 * target))))
        for label in self.bus_labels:
            self.canvas.itemconfigure(label, font=('TkDefaultFont', max(7, round(9 * target))))
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))

    def fit(self, event=None):
        if self.fitted and self.canvas.winfo_width() > 1 and self.canvas.winfo_height() > 1:
            self.scale_to(min(1., (self.canvas.winfo_width() - 12) / 920,
                              (self.canvas.winfo_height() - 12) / 560))
            self.canvas.xview_moveto(0)
            self.canvas.yview_moveto(0)

    def reset(self):
        self.scale = 1.
        self.fitted = True
        self.draw()
        self.fit()
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)

    def draw(self):
        self.canvas.delete('all')
        self.boxes, self.labels = {}, {}
        self.bus_labels = []
        for left, right, label in LINKS:
            x1, y1 = BY_ID[left].position
            x2, y2 = BY_ID[right].position
            self.canvas.create_line(x1 + 80, y1 + 35, x2 + 80, y2 + 35,
                                    fill='#64748b', width=2, tags=('bus',))
            self.bus_labels.append(self.canvas.create_text((x1 + x2) / 2 + 80, (y1 + y2) / 2 + 20,
                                    text=label, fill='#cbd5e1', font=('TkDefaultFont', 9)))
        for component in COMPONENTS:
            x, y = component.position
            tags = ('component:' + component.id,)
            self.boxes[component.id] = self.canvas.create_rectangle(
                x, y, x + 160, y + 75, fill=COLORS['unknown'], outline='#94a3b8', width=2, tags=tags)
            self.labels[component.id] = self.canvas.create_text(
                x + 80, y + 37, text=component.label + '\nunknown', fill='white',
                width=150, font=('TkDefaultFont', 11), tags=tags)
        self.canvas.configure(scrollregion=(0, 0, 920, 560))
        self.refresh()

    def refresh(self):
        now = time.monotonic() if self.view_time is None else self.view_time
        for component in COMPONENTS:
            observation = presentation(self.observations.view(component.id, now), component.id,
                                       running=self.observations.identity is not None, replay=self.playback is not None)
            self.canvas.itemconfigure(self.boxes[component.id], fill=COLORS[observation['state']],
                                      width=4 if component.id == self.selected else 2)
            self.canvas.itemconfigure(self.labels[component.id], text=component.label + '\n' + observation['state'])
        if self.selected:
            component = BY_ID[self.selected]
            observation = presentation(self.observations.view(self.selected, now), self.selected,
                                       running=self.observations.identity is not None, replay=self.playback is not None)
            text = (f'{component.label}\n\n{component.hardware}\n\n{component.fidelity}\n\n'
                            f'Reference: {component.reference}\n\n'
                            f'{observation["state"]}: {observation["detail"]}\n'
                            f'Source: {observation["source"] or "none"}\n'
                            f'Sample monotonic time: {observation["time"]}\n'
                            f'Dropped observations: {self.observations.dropped}')
            if text != self.detail_text:
                position = self.detail.yview()[0]
                self.detail.configure(state='normal')
                self.detail.delete('1.0', 'end')
                self.detail.insert('1.0', text)
                self.detail.configure(state='disabled')
                self.detail.yview_moveto(position)
                self.detail_text = text

    def tick(self):
        if self.playback is not None:
            self.view_time = min(time.monotonic() - self.playback_start, self.playback.duration)
            self.observations = self.playback.seek(self.view_time)
            self.mode.set(f'REPLAY • {self.view_time:.1f} / {self.playback.duration:.1f}s • '
                          + self.playback.data['identity'])
            self.refresh()
            self.timer = self.window.after(100, self.tick)
            return
        runtime = self.runtime()
        if runtime is not None and (runtime.process is None or runtime.process.poll() is not None):
            runtime = None
        if self.recording_active and (not runtime or runtime.identity != self.recording.data['identity']):
            self.stop_record()
            self.trace_status.set('Recording stopped: owned runtime changed. Save the retained observations.')
        self.collector.bind(runtime)
        self.observations.bind(runtime.identity if runtime else None)
        self.mode.set('LIVE • ' + (runtime.identity if runtime else 'Emulator off — press Start in Workbench')
                      + ' • SSH target telemetry is not supported')
        for event in self.collector.take() or ():
            if self.observations.accept(event) and self.recording_active:
                self.recording.append(event)
                if self.recording.full:
                    self.stop_record()
                    self.trace_status.set('Recording limit reached; retained observations can be saved.')
        self.observations.dropped = self.collector.dropped
        self.refresh()
        self.timer = self.window.after(100, self.tick)

    def record(self):
        if self.playback is not None or self.observations.identity is None:
            raise ValueError('Attach a running owned emulator in LIVE mode before recording')
        if self.recording is not None:
            raise ValueError('Save the retained recording before starting another')
        self.recording = Recording(self.observations.identity, time.monotonic())
        self.recording_start_dropped = self.collector.dropped
        self.recording_active = True
        self.trace_status.set('Recording observed state and activity; bounded to 10,000 observations.')

    def stop_record(self):
        if self.recording_active and self.recording is not None:
            self.recording.data['dropped'] += max(0, self.collector.dropped - self.recording_start_dropped)
        self.recording_active = False
        if self.recording is not None:
            self.trace_status.set(f'Recording stopped: {len(self.recording.data["events"])} observations retained.')

    def save_record(self):
        if self.recording is None:
            raise ValueError('No recording to save')
        self.stop_record()
        path = filedialog.asksaveasfilename(parent=self.window, title='Save observations (new file)',
                                          defaultextension='.json', filetypes=[('Observation recording', '*.json')])
        if path:
            self.recording.save(path)
            self.recording = None
            self.trace_status.set('Saved observation recording: ' + path)

    def can_close(self):
        return self.recording is None or messagebox.askyesno(
            'Unsaved schematic recording', 'Discard the retained observation recording?', parent=self.window)

    def close(self):
        if self.can_close():
            self.window.destroy()

    def discard_record(self):
        if self.can_close():
            self.recording_active = False
            self.recording = None
            self.trace_status.set('Recording discarded.')

    def load_replay(self):
        self.stop_record()
        path = filedialog.askopenfilename(parent=self.window, title='Replay observation recording',
                                         filetypes=[('Observation recording', '*.json')])
        if path:
            playback = Playback.load(path)
            self.collector.bind(None)
            self.playback = playback
            self.playback_start = time.monotonic()
            self.trace_status.set('REPLAY — recorded observations only; no emulator actions are dispatched.')

    def live(self):
        self.playback = None
        self.view_time = None
        self.observations = Observations()
        self.trace_status.set('LIVE — observing the currently owned runtime.')

    def destroyed(self, event):
        if event.widget is self.window and self.timer is not None:
            self.collector.close()
            self.window.after_cancel(self.timer)
            self.timer = None
