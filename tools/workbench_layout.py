"""Toolbar layout that keeps controls reachable as the host window narrows."""
from tkinter import ttk


class WrappingToolbar(ttk.Frame):
    """Accept ordinary packed children, then arrange them in measured rows."""
    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.pending = None
        self.signature = None
        self.rows = []
        self.bind('<Configure>', self.schedule, add='+')
        self.bind('<Map>', self.schedule, add='+')
        self.bind('<Destroy>', self.cancel, add='+')

    def schedule(self, event=None):
        if self.pending is None:
            self.pending = self.after_idle(self.reflow)

    def cancel(self, event=None):
        if event is not None and event.widget is not self:
            return
        if self.pending is not None:
            self.after_cancel(self.pending)
            self.pending = None

    def reflow(self):
        self.pending = None
        children = [child for child in self.winfo_children() if child not in self.rows]
        # Leave room for the existing frame padding and per-control gutters.
        available = max(1, self.winfo_width() - 16)
        signature = (available, tuple((str(child), child.winfo_reqwidth()) for child in children))
        if self.signature == signature:
            return
        self.signature = signature
        for child in children:
            child.pack_forget()
            if not getattr(child, '_wrap_observed', False):
                child.bind('<Configure>', self.schedule, add='+')
                child._wrap_observed = True
        for row in self.rows:
            row.destroy()
        self.rows = []
        used = 0
        for child in children:
            width = child.winfo_reqwidth() + 6
            if not self.rows or (used and used + width > available):
                row = ttk.Frame(self)
                row.pack(fill='x')
                self.rows.append(row)
                used = 0
            child.pack(in_=self.rows[-1], side='left', padx=3, pady=2)
            # The row is newer than its sibling controls in the stacking order.
            child.lift()
            used += width
