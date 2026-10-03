"""Optional setup uses invented names and never overwrites an existing portfolio."""
import pytest
from portfolio_app.onboarding import initial_categories, save_initial_categories
from portfolio_app.allocation import load_allocation
from portfolio_app.holdings import DataError
from portfolio_app.positions import save_position


def test_categories_can_have_unknown_or_parent_relative_targets(tmp_path):
    empty = initial_categories(['  Invented equities ', 'Invented gold'])
    assert [b.target for b in empty.buckets] == [None, None]
    config = save_initial_categories(tmp_path, ['Invented equities', 'Invented gold'], [80., 20.], None)
    assert not (tmp_path / 'holdings.csv').exists()
    loaded = load_allocation(tmp_path / 'allocation.yaml')
    assert loaded == config
    assert [b.target for b in loaded.buckets] == [.8, .2]
    assert len({b.id for b in loaded.buckets}) == 2


@pytest.mark.parametrize('names,targets', [([], None), (['A',' a '], None), (['A','B'], [50,None]),
    (['A','B'], [50,40]), (['A'], [float('nan')]), (['A'], [101])])
def test_invalid_setup_does_not_write(tmp_path, names, targets):
    with pytest.raises(DataError):
        save_initial_categories(tmp_path, names, targets, None)
    assert not (tmp_path / 'allocation.yaml').exists()


def test_setup_refuses_existing_allocation_and_concurrent_holdings(tmp_path):
    save_initial_categories(tmp_path, ['Invented reserve'], None, None)
    before = (tmp_path / 'allocation.yaml').read_bytes()
    with pytest.raises(DataError):
        save_initial_categories(tmp_path, ['Different'], None, None)
    assert (tmp_path / 'allocation.yaml').read_bytes() == before
    other = tmp_path / 'other'
    save_position(other / 'holdings.csv', dict(name='Invented position', shares='1'), expected_revision=None)
    with pytest.raises(DataError):
        save_initial_categories(other, ['Invented reserve'], None, None)
    assert not (other / 'allocation.yaml').exists()
