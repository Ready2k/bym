from super6.scoring import result_of, super6_points


def test_exact_score_is_five_points():
    assert super6_points(2, 1, 2, 1) == 5


def test_correct_result_wrong_score_is_two_points():
    assert super6_points(2, 1, 3, 0) == 2
    assert super6_points(1, 1, 2, 2) == 2
    assert super6_points(0, 1, 1, 3) == 2


def test_wrong_result_is_zero_points():
    assert super6_points(2, 1, 0, 0) == 0
    assert super6_points(1, 1, 2, 1) == 0


def test_result_of():
    assert result_of(2, 1) == "H"
    assert result_of(0, 0) == "D"
    assert result_of(1, 3) == "A"
