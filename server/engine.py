"""Tic-Tac-Toe³ rules engine (server side). Mirrors ENGINE() in index.html exactly.

L = 1 classic, 2 ultimate, 3 cubed.  Players: 1 = X, 2 = O, 3 = tie.
Cell index: L1 c | L2 b*9+c | L3 g*81+b*9+c.   Board index (bi): L2 b | L3 g*9+b.
"""
LINES = ((0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6))


def _line3(a, base, p):
    for x, y, z in LINES:
        if a[base + x] == p and a[base + y] == p and a[base + z] == p:
            return True
    return False


def _all_set(a, base):
    return all(a[base + i] for i in range(9))


class State:
    __slots__ = ("L", "cells", "bw", "cnt", "gw", "target", "turn", "winner", "moves", "worm")

    def __init__(self, L):
        n = {1: 9, 2: 81, 3: 729}[L]
        nb = {1: 1, 2: 9, 3: 81}[L]
        self.L = L
        self.cells = bytearray(n)
        self.bw = bytearray(nb)
        self.cnt = bytearray(nb)
        self.gw = bytearray(9)
        self.target = -1
        self.turn = 1
        self.winner = 0
        self.moves = 0
        self.worm = [0, 3, 3] if L == 3 else [0, 0, 0]

    # ---------- rules ----------
    def board_open(self, bi):
        return not self.bw[bi] and not (self.L == 3 and self.gw[bi // 9])

    def zone(self, worm=False):
        if self.L == 1:
            return ("board", 0)
        t = self.target
        if worm or t < 0:
            return ("any", None)
        if self.L == 2:
            return ("any", None) if self.bw[t] else ("board", t)
        g = t // 9
        if self.gw[g]:
            return ("any", None)
        if self.bw[t]:
            return ("game", g)
        return ("board", t)

    def is_legal(self, cell, worm=False):
        if self.winner or not isinstance(cell, int) or cell < 0 or cell >= len(self.cells) or self.cells[cell]:
            return False
        if worm and (self.L != 3 or self.worm[self.turn] <= 0):
            return False
        if self.L == 1:
            return True
        bi = cell // 9
        if not self.board_open(bi):
            return False
        kind, v = self.zone(worm)
        if kind == "board":
            return bi == v
        if kind == "game":
            return bi // 9 == v
        return True

    def legal(self, worm=False):
        return [c for c in range(len(self.cells)) if self.is_legal(c, worm)]

    def play(self, cell, worm=False):
        """Apply a move for self.turn. Caller must check is_legal first."""
        p = self.turn
        if worm:
            self.worm[p] -= 1
        self.cells[cell] = p
        self.moves += 1
        if self.L == 1:
            if _line3(self.cells, 0, p):
                self.winner = p
            elif self.moves == 9:
                self.winner = 3
            self.turn = 3 - p
            return
        bi, c = cell // 9, cell % 9
        self.cnt[bi] += 1
        if _line3(self.cells, bi * 9, p):
            self.bw[bi] = p
        elif self.cnt[bi] == 9:
            self.bw[bi] = 3
        if self.L == 2:
            if self.bw[bi]:
                if self.bw[bi] == p and _line3(self.bw, 0, p):
                    self.winner = p
                elif _all_set(self.bw, 0):
                    self.winner = 3
            self.target = c
        else:
            g, b = bi // 9, bi % 9
            if self.bw[bi]:
                if self.bw[bi] == p and _line3(self.bw, g * 9, p):
                    self.gw[g] = p
                elif _all_set(self.bw, g * 9):
                    self.gw[g] = 3
                if self.gw[g]:
                    if self.gw[g] == p and _line3(self.gw, 0, p):
                        self.winner = p
                    elif _all_set(self.gw, 0):
                        self.winner = 3
            self.target = b * 9 + c
        self.turn = 3 - p
