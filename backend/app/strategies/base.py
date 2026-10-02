"""Interface commune des stratégies.

Règle d'or anti look-ahead : le signal à l'index i ne doit utiliser que des
données d'index <= i (indicateurs calculés sur bougies closes).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum

import numpy as np


class Signal(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class Strategy(ABC):
    """Toute stratégie reçoit les bougies + paramètres et retourne des signaux."""

    name: str = "base"
    description: str = ""
    # Statut d'edge honnête, affiché dans le dashboard :
    #   "UNTESTED"            -> pas encore passée par IS/OOS + walk-forward
    #   "NO_EDGE_DEMONSTRATED" -> testée, aucun edge démontré (démo uniquement)
    #   "UNDER_REVIEW"        -> résultats partiels, ne pas trader dessus
    edge_status: str = "UNTESTED"
    edge_note: str = ""

    def __init__(self, params: dict | None = None):
        self.params = dict(self.default_params())
        if params:
            self.params.update(params)

    @classmethod
    def default_params(cls) -> dict:
        return {}

    @abstractmethod
    def generate(self, candles: dict[str, np.ndarray]) -> np.ndarray:
        """Retourne un tableau de signaux ('BUY'/'SELL'/'HOLD') aligné sur les bougies.

        `candles` : dict avec au minimum 'open', 'high', 'low', 'close', 'volume'.
        """
