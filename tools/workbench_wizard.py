"""Collect setup choices before running the existing asynchronous coordinator."""
from pathlib import Path
import re
import tkinter as tk
from tkinter import filedialog, ttk

from uconsole_emulator import DISPLAY_BACKENDS
from forge_audio import MODES as AUDIO_MODES


class Wizard:
    def __init__(self, app):
        self.app = app
        self.window = tk.Toplevel(app.root)
        self.window.title('Workbench configuration')
        self.window.geometry('800x650')
        self.index = 0
        self.values = {name: tk.StringVar(value=getattr(app, name).get()) for name in
                       ('mode', 'display', 'keyboard', 'audio', 'adc_reference', 'modem')}
        self.image = tk.StringVar()
        self.sha = tk.StringVar()
        self.source = tk.StringVar(value='recommended')
        self.packages = tk.BooleanVar(value=False)
        self.download = tk.BooleanVar(value=False)
        self.pages = []
        self.heading = ttk.Label(self.window, font=('TkDefaultFont', 14))
        self.heading.pack(anchor='w', padx=16, pady=12)
        self.body = ttk.Frame(self.window, padding=16)
        self.body.pack(fill='both', expand=True)
        page = self.page('Guest image')
        ttk.Label(page, text=f'Workspace: {app.workspace}\nExisting prepared workspaces are preserved and reused.',
                  wraplength=740).pack(anchor='w', pady=10)
        ttk.Radiobutton(page, text='Recommended: official uConsole CM4 v3.1 (64-bit)',
                        variable=self.source, value='recommended').pack(anchor='w')
        ttk.Radiobutton(page, text='Use my own image (only for an unprepared workspace)',
                        variable=self.source, value='custom').pack(anchor='w')
        ttk.Entry(page, textvariable=self.image).pack(fill='x', pady=6)
        ttk.Button(page, text='Browse image…', command=self.browse).pack(anchor='w')
        ttk.Label(page, text='Custom image SHA-256 (required):').pack(anchor='w', pady=6)
        ttk.Entry(page, textvariable=self.sha).pack(fill='x')
        page = self.page('Emulated hardware')
        options = [('mode', 'Boot mode', ('desktop', 'normal', 'maintenance')),
                   ('display', 'Display backend', DISPLAY_BACKENDS),
                   ('keyboard', 'Keyboard', ('generic', 'composite')),
                   ('audio', 'Audio surrogate', AUDIO_MODES),
                   ('adc_reference', 'ADC reference', ('fixed', 'missing')),
                   ('modem', 'Modem', ('none', 'composite'))]
        for name, label, choices in options:
            ttk.Label(page, text=label).pack(anchor='w', pady=(8, 0))
            ttk.Combobox(page, textvariable=self.values[name], values=choices,
                         state='readonly').pack(anchor='w')
        page = self.page('Permissions')
        from workbench_onboarding import LINUX_RUNTIME, LINUX_BUILD, MAC_RUNTIME, MAC_BUILD, IMAGE_URL
        import sys
        names = MAC_RUNTIME + MAC_BUILD if sys.platform == 'darwin' else LINUX_RUNTIME + LINUX_BUILD
        self.allowed_packages = frozenset(names)
        ttk.Checkbutton(page, text='Allow installation of missing prerequisite packages and their dependencies',
                        variable=self.packages).pack(anchor='w')
        ttk.Label(page, text=', '.join(names)+'\n\nOnly missing packages from this list will be requested. '
                  'Administrator authentication may still appear. Workbench never stores your password. '
                  'An active package transaction must finish even if you cancel later steps.',
                  wraplength=730).pack(anchor='w', pady=10)
        ttk.Checkbutton(page, text='Allow the recommended image download when needed (2.21 GB; 20 GiB free required)',
                        variable=self.download).pack(anchor='w')
        ttk.Label(page, text='The download is verified against a pinned SHA-256.\n'+IMAGE_URL+
                  '\n\nFinish may build the pinned emulator, import the image, prepare the guest desktop and boot. '
                  'No physical device is modified. SSH hosts are configured separately with Physical target.',
                  wraplength=730).pack(anchor='w', pady=10)
        page = self.page('Review and finish')
        self.review_text = tk.Text(page, wrap='word', height=20, state='disabled')
        self.review_text.pack(fill='both', expand=True)
        bar = ttk.Frame(self.window, padding=12)
        bar.pack(fill='x')
        ttk.Button(bar, text='Cancel', command=self.window.destroy).pack(side='right')
        self.next_button = ttk.Button(bar, text='Next', command=lambda: app.guard(self.next))
        self.next_button.pack(side='right', padx=8)
        self.back = ttk.Button(bar, text='Back', command=lambda: self.show(self.index-1))
        self.back.pack(side='left')
        self.show(0)

    def page(self, title):
        page = ttk.Frame(self.body)
        self.pages.append((title, page))
        return page

    def browse(self):
        value = filedialog.askopenfilename(parent=self.window, title='Choose Linux image')
        if value:
            self.image.set(value)
            self.source.set('custom')

    def show(self, index):
        self.index = index
        for _, page in self.pages:
            page.pack_forget()
        title, page = self.pages[index]
        self.heading.configure(text=f'{index+1} of {len(self.pages)} — {title}')
        page.pack(fill='both', expand=True)
        self.back.configure(state='disabled' if index == 0 else 'normal')
        self.next_button.configure(text='Finish' if index == len(self.pages)-1 else 'Next')
        if index == len(self.pages)-1:
            text = f'Workspace: {self.app.workspace}\nExisting prepared guest: reused, not replaced\n'
            text += '\n'.join(f'{key}: {value.get()}' for key, value in self.values.items())
            text += f'\nImage source for first use: {self.source.get()}\nCustom image: {self.image.get()}\nSHA-256: {self.sha.get()}\n'
            text += f'Install missing packages: {self.packages.get()}\nAllow image download: {self.download.get()}\n\n'
            text += 'Finish applies these settings and runs setup through emulator startup without further Workbench questions. '
            text += 'System authentication may be required. If an unapproved step is needed, setup stops with an error. '
            text += 'Cancel now discards all wizard changes.'
            self.review_text.configure(state='normal')
            self.review_text.delete('1.0', 'end')
            self.review_text.insert('1.0', text)
            self.review_text.configure(state='disabled')

    def next(self):
        if self.index < len(self.pages)-1:
            self.show(self.index+1)
            return
        self.app.check_startable()
        panel = getattr(self.app, 'setup_panel', None)
        if panel and panel.window.winfo_exists() and (panel.flow_active or panel.process is not None):
            raise ValueError('Wait for the current setup operation before finishing configuration')
        custom = None
        if self.source.get() == 'custom' and not (self.app.workspace / 'machine.json').is_file():
            path = Path(self.image.get()).expanduser().resolve()
            if not path.is_file() or not re.fullmatch('[0-9a-fA-F]{64}', self.sha.get().strip()):
                raise ValueError('Choose an existing custom image and enter its 64-digit SHA-256')
            custom = (str(path), self.sha.get().strip().lower())
        for name, value in self.values.items():
            getattr(self.app, name).set(value.get())
        self.app.show_setup()
        panel = self.app.setup_panel
        panel.wizard_consent = {'packages': self.allowed_packages if self.packages.get() else frozenset(),
                                'download': self.download.get(), 'custom': custom}
        panel.custom_image.set(custom is not None)
        panel.begin()
        self.window.destroy()
