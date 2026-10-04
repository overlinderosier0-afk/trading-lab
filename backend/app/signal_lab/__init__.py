"""Signal Lab — package d'analyse et de génération de signaux.

Principe honnête affiché partout dans l'UI :
    score / 100  =  force du modèle (somme pondérée de facteurs)
    score / 100  ≠  probabilité de gain

La "probabilité" réelle n'existe que calibrée sur l'historique des
signaux résolus (endpoint /api/signal-lab/stats).

Anti look-ahead : toutes les fonctions travaillent sur des bougies
CLOSES. L'appelant doit exclure la bougie en formation.
"""

from __future__ import annotations
