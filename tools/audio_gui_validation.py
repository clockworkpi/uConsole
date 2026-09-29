"""Exercise the real audio panel and shared jobs against an owned live VM."""
import time

from forge_controller import Controller


class GUIAudio:
    def __init__(self, runtime, output):
        import tkinter as tk
        from forge_audio_gui import AudioPanel
        self.root = tk.Tk()
        self.root.withdraw()
        self.controller = Controller({'audio': output}, ('device-control',),
                                     history=output / 'audio-gui-jobs.sqlite3')
        self.controller.runtimes['audio'] = runtime
        self.panel = AudioPanel(self.root, self.controller, 'audio')

    def operation(self, runtime, evidence, connected):
        # Invoke the same widgets as Workbench; do not bypass their submission
        # or terminal polling paths. Guest ALSA verification remains separate.
        self.panel.buttons[1 if connected else 2].invoke()
        job_id = self.panel.job
        if not job_id:
            raise ValueError('Audio panel did not submit a job: ' + self.panel.status.get())
        deadline = time.monotonic() + 20
        while self.panel.job is not None:
            if time.monotonic() > deadline:
                raise TimeoutError('Audio panel job deadline; inspect durable history')
            self.root.update()
            time.sleep(0.01)
        job = self.controller.job(job_id)
        if job['status'] != 'completed':
            raise ValueError('Audio panel job failed: ' + str(job))
        if job['context']['connected'] is not connected:
            raise ValueError('Audio panel submitted the wrong requested state')
        result = job['result']
        if result['observed']['connected'] is not connected:
            raise ValueError('Audio panel result does not confirm requested state')
        return {'frontend': 'tk-audio-panel', 'job': job,
                'displayed_result': self.panel.status.get()}

    def close(self):
        # The validator owns shutdown and its clean-filesystem checks. Drain
        # jobs before removing the runtime from this temporary controller.
        self.controller.executor.shutdown(wait=True)
        self.controller.runtimes.clear()
        self.controller.close()
        self.root.destroy()
