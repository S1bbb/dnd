import pytest

from app import dice


def test_simple_ranges():
    for _ in range(300):
        r = dice.roll("1d20+5")
        assert 6 <= r.total <= 25
        r = dice.roll("2d6-1")
        assert 1 <= r.total <= 11


def test_keep_highest():
    for _ in range(200):
        r = dice.roll("4d6kh3")
        g = r.groups[0]
        assert sum(g.kept) == 3
        assert r.total == sum(sorted(g.rolls)[1:])


def test_advantage_and_disadvantage():
    for _ in range(200):
        a = dice.roll("1d20+2", adv=1)
        assert a.total == max(a.groups[0].rolls) + 2
        d = dice.roll("1d20+2", adv=-1)
        assert d.total == min(d.groups[0].rolls) + 2
    assert "преим" in dice.roll("1d20", 1).formula


def test_russian_letters_and_percent():
    assert 1 <= dice.roll("к20").total <= 20
    assert 1 <= dice.roll("д100").total <= 100
    assert 1 <= dice.roll("d%").total <= 100


@pytest.mark.parametrize("bad", ["", "abc", "1d1", "1000d6", "d20 d20", "2d6kh5", "1d99999"])
def test_bad_formulas(bad):
    with pytest.raises(dice.DiceError):
        dice.roll(bad)


def test_to_dict_marks_dropped_dice():
    r = dice.roll("2d20kl1")
    d = r.to_dict()
    assert sum(1 for v in d["r"][0] if v < 0) == 1


def test_crit_doubles_damage_dice_not_bonus():
    r = dice.roll("1d6+4", crit=True)
    assert r.groups[0].count == 2 and r.bonus == 4
    assert 6 <= r.total <= 16
