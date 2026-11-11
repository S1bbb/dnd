"""Парсер и бросатель кубиков: 1d20+5, 2d6+1d4-1, 4d6kh3, 2d20kl1, d%."""
import re
import secrets
from dataclasses import dataclass, field

MAX_DICE = 100
MAX_SIDES = 1000

TERM_RE = re.compile(r"([+-])?\s*(?:(\d*)d(\d+|%)(?:(kh|kl)(\d+))?|(\d+))", re.I)


class DiceError(ValueError):
    pass


@dataclass
class Group:
    sign: int
    count: int
    sides: int
    keep: str | None = None  # 'kh' / 'kl'
    keep_n: int = 0
    rolls: list[int] = field(default_factory=list)
    kept: list[bool] = field(default_factory=list)

    @property
    def value(self) -> int:
        return self.sign * sum(r for r, k in zip(self.rolls, self.kept) if k)


@dataclass
class Roll:
    formula: str
    groups: list[Group]
    bonus: int
    total: int

    @property
    def d20(self) -> int | None:
        """Значение кубика d20, которое пошло в зачёт (для крит/провала)."""
        for g in self.groups:
            if g.sides == 20:
                kept = [r for r, k in zip(g.rolls, g.kept) if k]
                if len(kept) == 1:
                    return kept[0]
        return None

    @property
    def crit(self) -> bool:
        return self.d20 == 20

    @property
    def fumble(self) -> bool:
        return self.d20 == 1

    def dice_text(self) -> str:
        parts = []
        for g in self.groups:
            shown = []
            for r, k in zip(g.rolls, g.kept):
                shown.append(str(r) if k else f"~{r}~")
            parts.append(("-" if g.sign < 0 else "") + "[" + ", ".join(shown) + "]")
        if self.bonus:
            parts.append(f"{self.bonus:+d}")
        return " ".join(parts)

    def to_dict(self) -> dict:
        return {
            "f": self.formula,
            "t": self.total,
            "r": [[r if k else -r for r, k in zip(g.rolls, g.kept)] for g in self.groups],
            "s": [g.sides for g in self.groups],
            "b": self.bonus,
            "c": 1 if self.crit else (-1 if self.fumble else 0),
        }


def normalize(formula: str) -> str:
    f = formula.strip().lower().replace(" ", "").replace("к", "d").replace("д", "d")
    return f


def parse(formula: str) -> tuple[list[Group], int]:
    f = normalize(formula)
    if not f:
        raise DiceError("пустая формула")
    pos = 0
    groups: list[Group] = []
    bonus = 0
    total_dice = 0
    first = True
    while pos < len(f):
        m = TERM_RE.match(f, pos)
        if not m or m.end() == pos:
            raise DiceError(f"не понимаю «{formula}»")
        sign_s, cnt, sides, keep, keep_n, num = m.groups()
        if not first and not sign_s:
            raise DiceError(f"не понимаю «{formula}»")
        sign = -1 if sign_s == "-" else 1
        if num is not None:
            bonus += sign * int(num)
            if abs(bonus) > 10000:
                raise DiceError("слишком большой модификатор")
        else:
            count = int(cnt) if cnt else 1
            s = 100 if sides == "%" else int(sides)
            if count < 1 or s < 2:
                raise DiceError("странные кубики")
            if s > MAX_SIDES:
                raise DiceError(f"максимум d{MAX_SIDES}")
            total_dice += count
            if total_dice > MAX_DICE:
                raise DiceError(f"не больше {MAX_DICE} кубиков за раз")
            kn = int(keep_n) if keep_n else 0
            if keep and not (1 <= kn <= count):
                raise DiceError("неверное число оставляемых кубиков")
            groups.append(Group(sign, count, s, keep.lower() if keep else None, kn))
        pos = m.end()
        first = False
    if not groups and bonus == 0 and f != "0":
        raise DiceError(f"не понимаю «{formula}»")
    return groups, bonus


def roll(formula: str, adv: int = 0, crit: bool = False) -> Roll:
    """adv: 1 — преимущество, -1 — помеха (для первого 1d20 в формуле).
    crit: критический урон — количество кубиков удваивается."""
    groups, bonus = parse(formula)
    if crit:
        for g in groups:
            g.count *= 2
            if g.keep:
                g.keep_n *= 2
    if adv:
        for g in groups:
            if g.sides == 20 and g.count == 1 and not g.keep:
                g.count, g.keep, g.keep_n = 2, ("kh" if adv > 0 else "kl"), 1
                break
    for g in groups:
        g.rolls = [secrets.randbelow(g.sides) + 1 for _ in range(g.count)]
        g.kept = [True] * g.count
        if g.keep:
            order = sorted(range(g.count), key=lambda i: g.rolls[i], reverse=(g.keep == "kh"))
            keep_idx = set(order[: g.keep_n])
            g.kept = [i in keep_idx for i in range(g.count)]
    total = sum(g.value for g in groups) + bonus
    return Roll(pretty(groups, bonus, adv), groups, bonus, total)


def pretty(groups: list[Group], bonus: int, adv: int = 0) -> str:
    out = ""
    for g in groups:
        orig_count, keep = g.count, g.keep
        if adv and g.sides == 20 and g.keep_n == 1 and g.count == 2:
            orig_count, keep = 1, None
        term = f"{orig_count}d{g.sides}" + (f"{keep}{g.keep_n}" if keep else "")
        out += ("-" if g.sign < 0 else ("+" if out else "")) + term
    if bonus:
        out += f"{bonus:+d}"
    if adv:
        out += " (преим.)" if adv > 0 else " (помеха)"
    return out or "0"


def manual(formula: str, value: int) -> Roll:
    """Бросок настоящими кубиками: игрок сообщает, что выпало.
    Если в формуле один d20 — value это значение d20, остальное (модификатор) сервер добавит сам.
    Иначе value — сумма всех кубиков формулы."""
    groups, bonus = parse(formula)
    d20 = [g for g in groups if g.sides == 20 and g.count == 1]
    lo = sum(g.count for g in groups)
    hi = sum(g.count * g.sides for g in groups)
    if d20 and len(groups) >= 1:
        if not 1 <= value <= 20:
            raise DiceError("на d20 бывает от 1 до 20")
        for g in groups:
            if g is d20[0]:
                g.rolls = [value]
            else:
                g.rolls = [secrets.randbelow(g.sides) + 1 for _ in range(g.count)]
            g.kept = [True] * g.count
    else:
        if not lo <= value <= hi:
            raise DiceError(f"для {formula} на кубиках бывает от {lo} до {hi}")
        left = value
        for gi, g in enumerate(groups):
            g.rolls = []
            for k in range(g.count):
                rest = sum(x.count for x in groups[gi + 1:]) + (g.count - k - 1)
                v = max(1, min(g.sides, left - rest))
                g.rolls.append(v)
                left -= v
            g.kept = [True] * g.count
    total = sum(g.value for g in groups) + bonus
    return Roll(pretty(groups, bonus) + " (свои кубики)", groups, bonus, total)
