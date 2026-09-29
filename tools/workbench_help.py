"""Offline, searchable Tk user guide and keyboard-accessible contextual hints."""
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText


TOPICS = {
    'Getting started': """The Workbench is an image forge: edit and test a CM4 guest image, then take that image to real hardware. The bundled keyboard firmware is separate from the Linux guest image.

Press Start. Its progress window checks host prerequisites and the patched QEMU, offers to install missing packages, downloads the recommended official CM4 v3.1 Linux image when needed, verifies its pinned SHA-256, prepares the display, and continues to boot. Native packages include QEMU. You do not need to find an image or enter a checksum for the standard environment.

1. Press Start and approve the recommended 2.21 GB HTTPS image download. The completed download is cached and reverified before reuse. Interrupted downloads resume when the server supports byte ranges. A failed checksum prevents import. Allow at least 20 GiB free. For another image, expand Advanced tools and select Use my own Linux image; custom images still need a trusted checksum.
2. Preparation creates a private writable workspace; it never replaces an existing directory. Keep the entire workspace together, including its backing image. Allow at least 15–20 GiB, and more for exports and backups.
3. Desktop and a native graphical display are selected by default. Maintenance and headless operation remain explicit alternatives. The startup window stays open through emulator launch; Close dismisses that window without stopping the guest. Startup logs remain under emulator/startup-logs. Emulator launch is not proof that the guest has finished booting; watch its display and serial console.
4. Missing Debian/Ubuntu packages are listed for approval, then installed through the system authorization prompt; Workbench never collects your password or runs itself as root. On macOS with Homebrew, package installation runs as your user. Cancel prevents later steps but lets an active package transaction finish safely. Package removal and upgrades of already installed Debian packages are disabled. If repositories need maintenance or authentication is denied, the error and package-manager log are retained for retry.

5. Cancel startup requests cleanup and prevents subsequent stages; it does not undo already completed image changes. If boot finished before cancellation, the window reports that the VM remains running. Shut down the guest cleanly before exporting or changing its image.

Already have a workspace? Launch uconsole-workbench --workspace /absolute/path/to/workspace. It must contain machine.json and its referenced image and boot files. Do not create machine.json by hand or delete an existing workspace to silence an error.

Hover over controls for hints. Tab to a control and press F1 for its guide topic. This guide works offline; search matches both topic titles and their contents.""",
    'QEMU setup': """The Setup toolbar button opens a four-step configuration wizard: image, emulated hardware, permissions, and review. Finish applies the choices and runs setup through startup without more Workbench questions. System administrator authentication can still appear. Cancel discards wizard edits. Existing prepared workspaces are reused, never replaced. Configuration applies to the current Workbench session. SSH destinations are saved separately through Physical target; physical deployment authorization is never part of emulator setup.

Emulation requires the Workbench's patched QEMU, not just a generic system QEMU installation. Native packages include the matching emulator, runtime data, corresponding upstream source archive, builder and patch sources. The startup progress window's Advanced tools shows the selected emulator and expected patch fingerprint. Compatible user builds take precedence over the bundled emulator. System QEMU is never replaced.

Build / Rebuild emulator downloads the pinned release, verifies its SHA-256, applies the patches and builds in user-owned storage. Progress and output appear in the setup panel; the full build log is retained under emulator/build-logs. Cancel stops the build process group. Failed or cancelled builds do not select incomplete output. Builds are cached by source, patches and configuration. Building requires network access and development dependencies; running a packaged emulator does not require a compiler, but still needs its platform shared libraries.

Linux prerequisites include a C/C++ compiler, Ninja, pkg-config, patch, GLib, Pixman and libslirp development packages. Install GTK development libraries for a GTK display window. On Ubuntu these include build-essential, ninja-build, pkg-config, patch, libglib2.0-dev, libpixman-1-dev, libslirp-dev and libgtk-3-dev. macOS requires the command-line developer tools and corresponding Homebrew dependencies. Python 3.12+ and Tk are required for Workbench. Image import/export is supported on Linux/macOS, not Windows.

From a source checkout, make deps installs platform prerequisites and make build includes patched QEMU. make emulator-build builds just QEMU. Installed users can use the setup panel or the equivalent command below. Builds may take several minutes. Keep workspace backing files in place.

Start offers the official CM4 v3.1 64-bit image automatically from the manufacturer's HTTPS mirror. Its pinned checksum identifies the exact previously verified image, not a manufacturer signature or a guarantee of safety. Custom raw or modified images remain available under Advanced tools and require an independently verified SHA-256.""",
    'Boot and display': """Start boots the selected workspace, not the keyboard firmware shown in the editor.

The Guest terminal is an 80×24 VT-style terminal: click it to type directly, use Ctrl+C to interrupt the guest, and Ctrl+Shift+C/V or the context menu to copy/paste. Arrow and function keys work in terminal applications. On a serial login, use TERM=vt100 and stty rows 24 cols 80 if an application assumes another size. Window resizing does not change the guest serial terminal size. The single-line command field remains available. Save transcript preserves the complete raw serial log, including escape sequences; Copy terminal screen copies the rendered grid. Terminal output never controls your clipboard or opens links.

Maintenance starts a root shell directly, without systemd or a desktop. Later kernel messages can bury the root prompt: press Enter in the serial input field to redraw it. Normal starts the regular system. Desktop prepares the overlay for the surrogate display when needed, then boots it. This preparation changes the writable overlay; checkpoint important work first.

Display “none” is headless. Select an available host backend for a QEMU display window (for example GTK/SDL on Linux, Cocoa on macOS). Host support depends on the packaged QEMU build. Changing the selector affects the next boot.

Pause suspends the VM; Resume continues it. Neither shuts down the guest. Power off asks the guest to shut down. Forcing QEMU to stop is equivalent to removing power and can corrupt guest files.

The display is a 1280×720 mailbox-framebuffer surrogate, not native DSI/VC4/GPU emulation. A successful desktop boot is not proof of real display-driver or hardware compatibility.""",
    'Editor and guest files': """The upper editor edits HOST files. Open file selects a host file; Save (Ctrl-S or Command-S) saves the editor contents on the host. Opening a file is not deployment to the guest.

Copy to guest transfers the saved file into the running guest. Guest files browses and transfers through the owned guest connection. Wait for transfers and guest commands to finish before starting another operation.

The lower pane is a serial transcript, not a full terminal emulator. Enter a line in the entry below it and press Return to send it. Paste command lets you review input. Copy selection, Copy boot log and Save transcript provide text evidence without screenshots. Transcripts can contain guest secrets: review before sharing.

The bundled keyboard project is copied to a user-owned location. Your edited project is preserved when bundled source changes. Building keyboard firmware and modifying a Linux guest image are different workflows.""",
    'Images and checkpoints': """Import creates a new workspace from a supported CM4 image after checksum verification. A custom image must retain the supported partition layout and boot filenames. Import does not flash a physical device.

Stop the emulator before image operations. Image → Create checkpoint records a named recovery point. Restore checkpoint changes the writable image back to that point; review its confirmation carefully. Recover interrupted restore reconciles a previously interrupted image transaction. Refresh boot files extracts updated boot assets after changes inside the guest.

Export image produces a new raw SD-card image suitable for subsequent hardware qualification. An emulator boot alone does not qualify that image on hardware. Keep the source image, final export and validation evidence.

Cancel image job requests cancellation, not rollback. Wait for cleanup and inspect the recorded outcome before retrying. Never move an overlay separately from its backing image.""",
    'Physical target and recovery': """Physical target opens saved SSH hosts. Enter a hostname/IP or SSH alias and optional username, then Save target. Test connection checks existing SSH key access without sudo or target writes. Establish trust and keys in your terminal first. Saving or testing a host never grants deployment permission.

Deploy files and Deploy service use the host fields to capture a backup and prepare a reviewable plan. Review, approve, then Apply to hardware. Restore hardware restores the selected approved plan backup. The separate approved-plan dropdown lists plans for all hosts; changing host fields never redirects an existing plan. Recovery jobs and physical writes still require explicit approval.

The normal live-development workflow is SSH host/target with backup and restore of the existing card. A spare card or external reader is an optional deployment route, not a prerequisite.

Preserve a verified full backup before changing a device. Recovery preparation, approval, execution and reconciliation are distinct stages. Read each panel's current evidence and approval requirements. Never automatically retry an uncertain write or infer success merely because SSH returned.

Cancellation does not reverse completed writes. Retain failed journals, hashes and receipts. An independent reconciliation and normal-boot check are required before declaring a restore complete. Do not repair an original filesystem silently as part of backup/restore.""",
    'Power and device controls': """Next boot input selects generic USB input or the composite keyboard surrogate. Keyboard deck drives the owned emulated input bridge, not a physical keyboard.

Audio controls use USB audio surrogates: playback capture and synthetic input, not the carrier's native jack or a host microphone. Modem controls are a surrogate, not real cellular connectivity. Select these devices before boot.

ADC reference “missing” describes the guest supply reference, not converter power. These substitutes test guest behavior but do not establish electrical or native-device fidelity.

Load power profile captures an immutable initial power-state snapshot for the next boot. Clear profile restores defaults. Live power → Apply field changes one supported property; Read state queries it. Values are true/false or integers as indicated by the property. Key events can shut down the guest.

Run power schedule replays host-clock events. Cancel replay stops future events; completed events are not rolled back. Use checkpoints or disposable workspaces for fault injection.""",
    'Tasks and coding agents': """Tasks lists guest and host task definitions. A repository task file is not permission to execute arbitrary host commands. Host tasks require an owner-reviewed policy and its approved checksum. Guest tasks and transfers share the owned guest channel.

Copy agent context creates a text description you can give to an external coding CLI. Review it before sharing. The Workbench does not embed another assistant.

Agent attachment is opt-in via --agent-socket, --agent-allow and, when needed, --agent-files-root. Grants and approved policies constrain operations; an attachment never bypasses confirmation or transaction safety. Keep private sockets, policies, credentials and raw job data private.

Cancel host task, Cancel guest command, Cancel boot and Cancel transfer request cancellation of their respective jobs. Wait for cleanup. Cancellation is not proof that no effects occurred.""",
    'Live schematic': """The Live Schematic toolbar button opens a code-linked component map. Off means no running owned emulator; it does not describe the power state of an SSH target. Disabled means the virtual device was omitted at boot; select it in Setup before the next boot. Disconnected means a configured USB device was detached; component details explain its connection controls. Waiting means no sample yet, unavailable means a failed observation, and stale means an old sample. Physical SSH telemetry is not supported.

Click a component or select it in the searchable list to inspect its implementation in the Host source editor. The source selector offers related implementations. Unsaved edits are protected; diagram sources open as inspection copies and Save asks for a destination.

Open schematic sheet shows the repository's hardware drawings offline. Choose a page, zoom, drag or scroll to pan, and search printed component/net names. Blue reference boxes link to functional implementations, not electrical simulations. The title identifies the original PDF and its SHA-256. Shared mainboard drawings do not prove which optional circuits are populated on a particular CM4 device.

LIVE observes only the emulator owned by this Workbench. Unknown means no suitable observation exists; stale means an earlier sample has expired. Blue shows observed presence, green recent observed activity, and gray unavailable state. Storage pulses use block-byte counters, keyboard pulses successful HID deliveries, and display pulses framebuffer redraw notifications (including possible host invalidation). Power pulses reflect changed sampled values, not traced I²C traffic. Audio/modem attachment is not evidence of audio/radio traffic. Older QEMU builds without counters show that limitation; rebuild the packaged patched QEMU to add counters.

Record observations retains at most 10,000 observations and 16 MiB. Stop recording then Save recording to a new JSON file. Runtime changes stop recording automatically; retained observations remain available to save. Discard recording is explicit. Review recordings before sharing because they contain runtime identifiers and sampled state.

Replay recording displays the retained observations on their recorded timeline, clearly labeled REPLAY. It never presses keys, changes power, or controls a guest. Return to live resumes observation. This differs from Run power schedule, which actually applies reviewed power changes to the emulator. Source navigation remains available during replay.""",
    'Errors and diagnostics': """Errors are recorded as timestamped JSON lines with severity, event, operation and workspace context. Help → Diagnostics shows the log path and lets you copy recent records. Error windows have selectable details and a Copy text button.

If Start reports missing machine.json, no guest workspace has been prepared at the selected path. Use Image → Import guest image, or restart with --workspace pointing at an existing prepared workspace. Installing keyboard firmware does not install a Linux guest image.

For boot failures, also inspect the workspace serial.log and workbench-qemu.log. Application errors, guest serial output and durable job history serve different purposes. Keep the job ID and original evidence when reporting a failure.

Logs and transcripts may include paths or error text from external commands. Review before sharing; do not post credentials, policies, disk images or full private job records. No diagnostic data is uploaded automatically.""",
}

# Text labels are shared by the actual controls; unknown controls retain default F1 help.
GROUPS = {
    'Live schematic': ('Record observations', 'Stop recording', 'Save recording…', 'Discard recording',
                       'Replay recording…', 'Return to live', 'Open schematic sheet…', 'Open source in Host editor'),
    'Boot and display': ('Start', 'Pause', 'Resume', 'Power off', 'Cancel boot'),
    'Editor and guest files': ('Open file', 'Save', 'Copy to guest', 'Guest files', 'Copy selection', 'Copy boot log', 'Paste command', 'Save transcript', 'Cancel transfer'),
    'Images and checkpoints': ('Image', 'Export image'),
    'Physical target and recovery': ('Physical target', 'Recovery jobs'),
    'Power and device controls': ('Keyboard deck', 'Audio controls', 'Modem controls', 'Load power profile', 'Clear profile', 'Apply field', 'Read state', 'Run power schedule', 'Cancel replay'),
    'Tasks and coding agents': ('Tasks', 'Copy agent context', 'Cancel guest command', 'Cancel host task'),
}
HINTS = {
    'Start': 'Boot a prepared Linux guest workspace. Import an image first; F1 for setup.',
    'Power off': 'Request guest shutdown. Forced stopping can corrupt the image.',
    'Save': 'Save the host source file; this does not deploy it to the guest.',
    'Copy to guest': 'Transfer the saved host file into the running guest.',
    'Export image': 'Export a stopped workspace to a new raw SD image.',
    'Physical target': 'Reviewed SSH host/target operations. Back up the existing card first.',
    'Recovery jobs': 'Prepare, review and reconcile real-device backup/restore transactions.',
}


class Tooltip:
    def __init__(self, widget, text):
        self.widget, self.text = widget, text
        self.timer = self.window = None
        widget.bind('<Enter>', self.schedule, add='+')
        widget.bind('<Leave>', self.hide, add='+')
        widget.bind('<FocusOut>', self.hide, add='+')
        widget.bind('<ButtonPress>', self.hide, add='+')
        widget.bind('<Destroy>', self.hide, add='+')
        widget.bind('<Escape>', self.hide, add='+')

    def schedule(self, event=None):
        self.hide()
        self.timer = self.widget.after(600, self.show)

    def show(self):
        self.timer = None
        if not self.widget.winfo_exists():
            return
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        ttk.Label(self.window, text=self.text, padding=8, wraplength=360,
                  relief='solid').pack()
        self.window.update_idletasks()
        x = min(self.widget.winfo_rootx(), self.widget.winfo_screenwidth() - self.window.winfo_reqwidth())
        y = min(self.widget.winfo_rooty() + self.widget.winfo_height() + 4,
                self.widget.winfo_screenheight() - self.window.winfo_reqheight())
        self.window.geometry(f'+{max(0, x)}+{max(0, y)}')

    def hide(self, event=None):
        if self.timer is not None:
            self.widget.after_cancel(self.timer)
            self.timer = None
        if self.window is not None:
            self.window.destroy()
            self.window = None


class Guide:
    def __init__(self, parent, topic='Getting started', setup_command=None):
        self.content = dict(TOPICS)
        if setup_command:
            self.content['QEMU setup'] += '\n\nInstalled builder command:\n\n' + setup_command
        self.window = tk.Toplevel(parent)
        self.window.title('uConsole Workbench — User guide')
        self.window.geometry('900x650')
        self.window.minsize(620, 420)
        bar = ttk.Frame(self.window, padding=10)
        bar.pack(fill='x')
        ttk.Label(bar, text='Search guide:').pack(side='left')
        self.query = tk.StringVar()
        self.search = ttk.Entry(bar, textvariable=self.query)
        self.search.pack(side='left', fill='x', expand=True, padx=8)
        ttk.Button(bar, text='Clear', command=lambda: self.query.set('')).pack(side='left')
        ttk.Button(bar, text='Copy topic', command=self.copy_topic).pack(side='left', padx=6)
        panes = ttk.Panedwindow(self.window, orient='horizontal')
        panes.pack(fill='both', expand=True, padx=10, pady=(0, 10))
        self.topics = tk.Listbox(panes, exportselection=False, width=28)
        panes.add(self.topics, weight=1)
        self.body = ScrolledText(panes, wrap='word', padx=18, pady=16,
                                 font='TkDefaultFont', state='disabled')
        self.body.tag_configure('title', font=('TkDefaultFont', 18, 'bold'), spacing3=18)
        self.body.tag_configure('body', spacing1=4, spacing3=8)
        self.body.tag_configure('match', background='#fff2a8', foreground='#111111')
        panes.add(self.body, weight=4)
        self.topics.bind('<<ListboxSelect>>', self.select)
        self.query.trace_add('write', self.filter)
        self.window.bind('<Escape>', lambda event: self.window.destroy())
        self.filter()
        self.show(topic)

    def filter(self, *args):
        query = self.query.get().casefold().strip()
        self.matches = [title for title, body in self.content.items() if query in (title + '\n' + body).casefold()]
        self.topics.delete(0, 'end')
        for title in self.matches:
            self.topics.insert('end', title)
        self.show(self.matches[0] if self.matches else None)

    def show(self, topic):
        self.body.configure(state='normal')
        self.body.delete('1.0', 'end')
        self.body.insert('end', (topic or 'No matching topics') + '\n', 'title')
        self.body.insert('end', self.content.get(topic, 'Try a different search or choose Clear.'), 'body')
        query = self.query.get().strip()
        if query:
            start = '1.0'
            while True:
                start = self.body.search(query, start, stopindex='end', nocase=True)
                if not start:
                    break
                end = f'{start}+{len(query)}c'
                self.body.tag_add('match', start, end)
                start = end
        self.body.configure(state='disabled')
        self.topics.selection_clear(0, 'end')
        if topic in self.matches:
            index = self.matches.index(topic)
            self.topics.selection_set(index)
            self.topics.see(index)

    def select(self, event=None):
        selected = self.topics.curselection()
        if selected:
            self.show(self.matches[selected[0]])

    def copy_topic(self):
        self.window.clipboard_clear()
        self.window.clipboard_append(self.body.get('1.0', 'end-1c'))


def attach_hints(parent, show_help):
    hints = []
    for widget in parent.winfo_children():
        hints.extend(attach_hints(widget, show_help))
        label = str(widget.cget('text')) if 'text' in widget.keys() else ''
        for topic, labels in GROUPS.items():
            if label in labels:
                hints.append(Tooltip(widget, HINTS.get(label, f'{label} — see {topic}. Press F1 for instructions and safety notes.')))
                widget.bind('<F1>', lambda event, name=topic: (show_help(name), 'break')[1])
    return hints
