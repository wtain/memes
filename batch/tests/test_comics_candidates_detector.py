from batch.utils.comics_candidates import DATETIME, EPOCH, group_candidates, parse_timestamp, window_pairs


def test_parse_datetime_shape_anywhere_in_the_name():
    shape, ts = parse_timestamp("IMG_20200923_110706_156.jpg")
    assert shape == DATETIME
    _, later = parse_timestamp("IMG_20200923_110708_392.jpg")
    assert later - ts == 2


def test_parse_epoch_milliseconds():
    shape, ts = parse_timestamp("FB_IMG_1746890432471.jpg")
    assert shape == EPOCH and ts == 1746890432.471


def test_invalid_datetimes_are_not_timestamps():
    assert parse_timestamp("IMG_20201323_110706.jpg") is None  # month 13
    assert parse_timestamp("IMG_20200923_250706.jpg") is None  # hour 25
    assert parse_timestamp("IMG_19990923_110706.jpg") is None  # year outside 2000-2099


def test_epoch_needs_exactly_thirteen_digits_in_range():
    assert parse_timestamp("x_17468904324712.jpg") is None  # 14 digits
    assert parse_timestamp("x_174689043247.jpg") is None  # 12 digits
    assert parse_timestamp("x_9746890432471.jpg") is None  # year 2250
    assert parse_timestamp("x_0946890432471.jpg") is None  # year 2000


def test_no_timestamp():
    assert parse_timestamp("meme.jpg") is None


def _item(name, ts, shape=DATETIME):
    return (name, f"{name}.jpg", shape, float(ts))


def _group(items, distances, gap=5.0, lo=0.06, hi=0.15, max_group=6):
    return group_candidates(items, lambda a, b: distances.get((a, b), distances.get((b, a))), gap, lo, hi, max_group)


def test_known_pair_forms_one_group_whatever_the_sizes():
    result = _group([_item("a", 0), _item("b", 2)], {("a", "b"): 0.0998})
    assert [g.image_ids for g in result.groups] == [["a", "b"]]
    assert result.groups[0].max_gap == 2 and result.groups[0].max_distance == 0.0998
    assert result.pairs_linked == 1


def test_distance_outside_the_window_links_nothing():
    items = [_item("a", 0), _item("b", 1)]
    assert _group(items, {("a", "b"): 0.05}).groups == []
    assert _group(items, {("a", "b"): 0.16}).groups == []
    assert _group(items, {}).groups == []  # no embedding


def test_gap_above_the_limit_links_nothing():
    assert _group([_item("a", 0), _item("b", 5.5)], {("a", "b"): 0.1}).groups == []
    assert len(_group([_item("a", 0), _item("b", 5)], {("a", "b"): 0.1}).groups) == 1


def test_chain_forms_one_group_with_the_weakest_edge_values():
    items = [_item("a", 0), _item("b", 4), _item("c", 8)]
    result = _group(items, {("a", "b"): 0.08, ("b", "c"): 0.14})
    (group,) = result.groups
    assert group.image_ids == ["a", "b", "c"]
    assert group.max_gap == 4 and group.max_distance == 0.14
    assert result.pairs_linked == 2


def test_oversized_component_is_dropped_and_counted():
    items = [_item(n, i) for i, n in enumerate("abcd")]
    distances = {(x, y): 0.1 for x in "abcd" for y in "abcd" if x < y}
    result = _group(items, distances, max_group=3)
    assert result.groups == [] and result.dropped_oversized == 1


def test_shapes_are_never_mixed():
    items = [_item("a", 0, DATETIME), _item("b", 1, EPOCH)]
    assert list(window_pairs(items, 5)) == []
    assert _group(items, {("a", "b"): 0.1}).groups == []


def test_group_is_ordered_by_timestamp_then_file_name():
    items = [_item("z", 3), _item("b", 1), _item("a", 1)]
    distances = {("a", "b"): 0.1, ("b", "z"): 0.1, ("a", "z"): 0.1}
    assert _group(items, distances).groups[0].image_ids == ["a", "b", "z"]
