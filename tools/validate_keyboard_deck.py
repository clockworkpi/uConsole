"""Drive real Tk deck buttons on a runtime already owned by the validator."""
import time
import tkinter as tk

from forge_controller import Controller
from forge_keyboard_gui import KeyboardDeck


def exercise(runtime, oracle, record, *, host_typing=False, pointer=False, select_scroll=False):
    root = tk.Tk()
    root.withdraw()
    controller = Controller({'gui': runtime.workspace}, grants=('device-control',),
                            keyboard_oracle=oracle)
    # This exact runtime was created by our caller; never discover/adopt a VM.
    controller.runtimes['gui'] = runtime
    record.update(validation='running', jobs=[], held_states=[],
                  input_path='focused-pointer' if pointer else 'focused-host-typing' if host_typing else 'deck-buttons')
    error = []
    contacts = [('matrix', 4, 2), ('matrix', 4, 2), ('matrix', 7, 2),
                ('matrix', 1, 0), ('matrix', 7, 2), ('matrix', 1, 0)]
    host_events = [('<KeyPress>', 'a'), ('<KeyRelease>', 'a'),
                   ('<KeyPress>', 'F1'), ('focus-out', None)]
    pointer_events = ['<Motion>', '<ButtonPress-1>', '<ButtonRelease-1>',
                      '<ButtonPress-2>', '<ButtonRelease-2>', '<ButtonPress-3>', 'focus-out']
    if select_scroll:
        pointer_events[1:1] = ['<Button-4>', '<Button-5>']
    deadline = time.monotonic() + 20
    active = None
    position = 0
    try:
        deck = KeyboardDeck(root, controller, 'gui')
        deck.select_scroll.set(select_scroll)
        if pointer:
            deck.pointer.focus_force()
            root.update()
            if root.focus_get() != deck.pointer:
                raise ValueError('Pointer pad did not acquire focus')
            deck.pointer.event_generate('<Motion>', x=10, y=40)
        if host_typing:
            deck.typing.focus_force()
            root.update()
            if root.focus_get() != deck.typing:
                raise ValueError('Typing area did not acquire focus')

        def advance():
            nonlocal position, active
            try:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Deck GUI action deadline expired')
                if deck.failed:
                    raise ValueError('Deck reported uncertain input: ' + deck.status.get())
                if deck.job is not None:
                    root.after(25, advance)
                    return
                if active:
                    result = controller.job(active)
                    record['jobs'].append(result)
                    record['held_states'].append(sorted(deck.held))
                    if result['status'] != 'completed':
                        raise ValueError('Deck action did not complete')
                    active = None
                count = len(pointer_events) if pointer else len(host_events) if host_typing else len(contacts)
                if position == count:
                    if deck.held or deck.host_keys.held or deck.host_queue or deck.pending_releases:
                        raise ValueError('Deck retained held contacts after release')
                    record['geometry'] = deck.window.geometry()
                    deck.close()
                    if deck.window.winfo_exists():
                        raise ValueError('Released deck refused to close')
                    record['validation'] = 'passed'
                    root.quit()
                    return
                previous = set(controller.jobs)
                if pointer:
                    event = pointer_events[position]
                    if event == 'focus-out':
                        deck.window.focus_force()
                    else:
                        deck.pointer.event_generate(event, x=26, y=52)
                    root.update()
                elif host_typing:
                    event, keysym = host_events[position]
                    if event == 'focus-out':
                        deck.window.focus_force()
                    else:
                        deck.typing.event_generate(event, keysym=keysym)
                    root.update()  # Dispatch focus events and idle-delayed releases.
                else:
                    deck.buttons[contacts[position]][0].invoke()
                submitted = set(controller.jobs) - previous
                if len(submitted) != 1:
                    raise ValueError('GUI event did not submit exactly one job: ' + deck.status.get())
                active = submitted.pop()
                position += 1
                root.after(25, advance)
            except BaseException as exc:
                error.append(exc)
                record.update(validation='failed', error=str(exc))
                root.quit()

        root.after(0, advance)
        root.mainloop()
        if error:
            raise error[0]
        if record['validation'] != 'passed':
            raise ValueError('Deck event loop ended before validation completed')
    finally:
        # Outer validator owns VM cleanup; this controller owns only its oracle/jobs.
        controller.runtimes.clear()
        try:
            controller.close()
        finally:
            root.destroy()
