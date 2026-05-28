"""
Testy jednostkowe — ETL i optymalizacja tras.
Uruchom: pytest tests/ -v
"""
import pytest
from app.services.route_optimizer import RouteOptimizer, ItemDoPick


def make_item(pid, symbol, loc, x, y, qty=1.0):
    return ItemDoPick(
        pozycja_id=pid, towar_id=pid, towar_symbol=symbol,
        towar_nazwa=f"Towar {symbol}", ilosc_wymagana=qty,
        lokalizacja_kod=loc, lokalizacja_x=x, lokalizacja_y=y
    )


class TestRouteOptimizer:
    def setup_method(self):
        self.optimizer = RouteOptimizer(entry_point=(0.0, 0.0))

    def test_empty_list(self):
        result = self.optimizer.optimize([])
        assert result.kolejnosc == []
        assert result.szacowana_odleglosc_m == 0.0

    def test_single_item(self):
        items = [make_item(1, "TOW001", "A01-001", 2.0, 1.0)]
        result = self.optimizer.optimize(items)
        assert len(result.kolejnosc) == 1
        assert result.liczba_lokalizacji == 1

    def test_nearest_neighbor_order(self):
        """Sprawdza czy NN wybiera bliższe lokalizacje najpierw."""
        items = [
            make_item(1, "TOW001", "A01-001", 1.0, 0.0),   # blisko wejścia
            make_item(2, "TOW002", "C05-003", 100.0, 100.0),  # daleko
            make_item(3, "TOW003", "A01-002", 1.0, 1.0),   # blisko wejścia
        ]
        result = self.optimizer.optimize(items, algorithm="nearest_neighbor")
        # Pierwsze dwa powinny być z lokalizacji blisko (0,0)
        first_loc = result.kolejnosc[0].lokalizacja_x
        assert first_loc < 10.0, "Pierwszy towar powinien być bliski wejścia"

    def test_grouping_by_location(self):
        """Towary z tej samej lokalizacji powinny być zebrane razem."""
        items = [
            make_item(1, "TOW001", "A01-001", 2.0, 1.0),
            make_item(2, "TOW002", "C05-003", 8.0, 12.0),
            make_item(3, "TOW003", "A01-001", 2.0, 1.0),  # ta sama lokalizacja co TOW001
        ]
        result = self.optimizer.optimize(items)
        # TOW001 i TOW003 powinny być obok siebie w kolejności
        locs = [item.lokalizacja_kod for item in result.kolejnosc]
        # Oba wpisy A01-001 powinny być razem
        idx1 = locs.index("A01-001")
        indices = [i for i, l in enumerate(locs) if l == "A01-001"]
        assert max(indices) - min(indices) == len(indices) - 1, \
            "Towary z tej samej lokalizacji powinny być razem"

    def test_distance_calculated(self):
        """Odległość powinna być > 0 dla towarów dalej od wejścia."""
        items = [make_item(1, "TOW001", "B03-002", 5.0, 6.0)]
        result = self.optimizer.optimize(items)
        # Odległość: wejście (0,0) → (5,6) → (0,0) = 2 × sqrt(25+36) ≈ 15.6m
        assert result.szacowana_odleglosc_m > 10.0

    def test_snake_algorithm(self):
        """Snake algorithm powinien działać bez błędów."""
        items = [
            make_item(1, "TOW001", "A01-001", 2.0, 0.0),
            make_item(2, "TOW002", "A01-002", 2.0, 1.0),
            make_item(3, "TOW003", "B01-001", 5.0, 0.0),
        ]
        result = self.optimizer.optimize(items, algorithm="snake")
        assert len(result.kolejnosc) == 3
        assert result.algorytm == "snake"


class TestItemDoPick:
    def test_pozostalo_property(self):
        item = make_item(1, "TOW001", "A01", 1.0, 1.0, qty=10.0)
        item.ilosc_zebrana = 3.0
        assert item.pozostalo == 7.0

    def test_potwierdzone_default_false(self):
        item = make_item(1, "TOW001", "A01", 1.0, 1.0)
        assert item.potwierdzone is False
