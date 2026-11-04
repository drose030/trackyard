import demucs.apply

from trackyard.progress import Progress, demucs_segments


def test_demucs_segments_report_fractions_of_the_current_step():
    seen = []
    progress = Progress(lambda step, fraction: seen.append((step, fraction)))
    progress("Separating", 0.0)
    with demucs_segments(progress):
        items = list(demucs.apply.tqdm.tqdm(range(4), unit="seconds"))
    assert items == [0, 1, 2, 3]
    assert [f for _, f in seen] == [0.0, 0.0, 0.25, 0.5, 0.75, 1.0]
    assert {s for s, _ in seen} == {"Separating"}
    assert demucs.apply.tqdm.__name__ == "tqdm"
