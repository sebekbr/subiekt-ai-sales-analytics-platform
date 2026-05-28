"""
Optymalizacja trasy kompletacji zamówień w magazynie.
Algorytm Nearest Neighbor (NN) — prosty, szybki, skuteczny dla magazynów < 1000 lok.
Dla > 200 pozycji użyj trybu 'snake' (serpentynowy) jako alternatywa.
"""
import logging
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class ItemDoPick:
    """Pojedyncza pozycja do zebrania."""
    pozycja_id: int
    towar_id: int
    towar_symbol: str
    towar_nazwa: str
    ilosc_wymagana: float
    lokalizacja_kod: str
    lokalizacja_x: float
    lokalizacja_y: float
    ean: Optional[str] = None
    ilosc_zebrana: float = 0.0
    potwierdzone: bool = False
    blad: bool = False

    @property
    def pozostalo(self) -> float:
        return self.ilosc_wymagana - self.ilosc_zebrana


@dataclass
class TrasakompletacjiWynik:
    """Wynik optymalizacji trasy."""
    kolejnosc: List[ItemDoPick]
    szacowana_odleglosc_m: float
    liczba_lokalizacji: int
    liczba_pozycji: int
    algorytm: str


class RouteOptimizer:
    """
    Optymalizator trasy kompletacji.
    
    Wejście magazynu przyjmuje się jako punkt (0, 0).
    Koordynaty X, Y są w metrach od wejścia.
    """

    def __init__(self, entry_point: Tuple[float, float] = (0.0, 0.0)):
        self.entry_x, self.entry_y = entry_point

    def _distance(self, x1: float, y1: float, x2: float, y2: float) -> float:
        return float(np.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2))

    def _group_by_location(self, items: List[ItemDoPick]) -> Dict[str, List[ItemDoPick]]:
        """Grupuje pozycje po lokalizacji — wiele towarów z jednego miejsca."""
        groups = {}
        for item in items:
            groups.setdefault(item.lokalizacja_kod, []).append(item)
        return groups

    def optimize_nearest_neighbor(self, items: List[ItemDoPick]) -> TrasakompletacjiWynik:
        """
        Algorytm Nearest Neighbor.
        Złożoność: O(n²) — dla n=200 lokalizacji ~0.1ms.
        """
        if not items:
            return TrasakompletacjiWynik([], 0.0, 0, 0, "nearest_neighbor")

        groups = self._group_by_location(items)
        locations = list(groups.keys())

        # Pobierz koordynaty
        coords = {}
        for loc, loc_items in groups.items():
            coords[loc] = (loc_items[0].lokalizacja_x, loc_items[0].lokalizacja_y)

        unvisited = set(locations)
        route = []
        total_dist = 0.0
        cur_x, cur_y = self.entry_x, self.entry_y

        while unvisited:
            nearest = min(
                unvisited,
                key=lambda loc: self._distance(cur_x, cur_y, coords[loc][0], coords[loc][1])
            )
            dist = self._distance(cur_x, cur_y, coords[nearest][0], coords[nearest][1])
            total_dist += dist
            route.append(nearest)
            cur_x, cur_y = coords[nearest]
            unvisited.remove(nearest)

        # Dodaj powrót do wejścia
        total_dist += self._distance(cur_x, cur_y, self.entry_x, self.entry_y)

        # Spłaszcz do listy pozycji zachowując kolejność
        result_items = []
        for loc in route:
            result_items.extend(groups[loc])

        return TrasakompletacjiWynik(
            kolejnosc=result_items,
            szacowana_odleglosc_m=round(total_dist, 1),
            liczba_lokalizacji=len(locations),
            liczba_pozycji=len(items),
            algorytm="nearest_neighbor"
        )

    def optimize_snake(self, items: List[ItemDoPick]) -> TrasakompletacjiWynik:
        """
        Algorytm serpentynowy (snake) — optymalny dla magazynów z rzędami równoległymi.
        Przechodzi rzędy z-kola: A→, B←, C→ itd.
        Wymaga poprawnie skonfigurowanych koordynat X (rząd) i Y (pozycja w rzędzie).
        """
        if not items:
            return TrasakompletacjiWynik([], 0.0, 0, 0, "snake")

        groups = self._group_by_location(items)

        def snake_key(loc: str):
            item = groups[loc][0]
            col = round(item.lokalizacja_x)     # kolumna rzędu (zaokrąglona)
            row_pos = item.lokalizacja_y
            # Nieparzyste kolumny — odwrócona kolejność Y (serpentyna)
            return (col, row_pos if col % 2 == 0 else -row_pos)

        sorted_locations = sorted(groups.keys(), key=snake_key)

        # Oblicz przybliżoną odległość
        total_dist = 0.0
        cur_x, cur_y = self.entry_x, self.entry_y
        for loc in sorted_locations:
            lx = groups[loc][0].lokalizacja_x
            ly = groups[loc][0].lokalizacja_y
            total_dist += self._distance(cur_x, cur_y, lx, ly)
            cur_x, cur_y = lx, ly
        total_dist += self._distance(cur_x, cur_y, self.entry_x, self.entry_y)

        result_items = []
        for loc in sorted_locations:
            result_items.extend(groups[loc])

        return TrasakompletacjiWynik(
            kolejnosc=result_items,
            szacowana_odleglosc_m=round(total_dist, 1),
            liczba_lokalizacji=len(sorted_locations),
            liczba_pozycji=len(items),
            algorytm="snake"
        )

    def optimize(self, items: List[ItemDoPick], algorithm: str = "nearest_neighbor") -> TrasakompletacjiWynik:
        """
        Wybór algorytmu optymalizacji.
        - 'nearest_neighbor': uniwersalny, dobry dla nieregularnych magazynów
        - 'snake': lepszy dla magazynów z równoległymi rzędami
        """
        if algorithm == "snake":
            return self.optimize_snake(items)
        return self.optimize_nearest_neighbor(items)


def serialize_trasa(wynik: TrasakompletacjiWynik) -> dict:
    """Serializuje wynik optymalizacji do formatu JSON dla API."""
    return {
        "algorytm": wynik.algorytm,
        "szacowana_odleglosc_m": wynik.szacowana_odleglosc_m,
        "liczba_lokalizacji": wynik.liczba_lokalizacji,
        "liczba_pozycji": wynik.liczba_pozycji,
        "pozycje": [
            {
                "lp": idx + 1,
                "pozycja_id": item.pozycja_id,
                "towar_id": item.towar_id,
                "towar_symbol": item.towar_symbol,
                "towar_nazwa": item.towar_nazwa,
                "ean": item.ean,
                "lokalizacja": item.lokalizacja_kod,
                "lokalizacja_x": item.lokalizacja_x,
                "lokalizacja_y": item.lokalizacja_y,
                "ilosc_wymagana": item.ilosc_wymagana,
                "ilosc_zebrana": item.ilosc_zebrana,
                "potwierdzone": item.potwierdzone,
            }
            for idx, item in enumerate(wynik.kolejnosc)
        ]
    }
