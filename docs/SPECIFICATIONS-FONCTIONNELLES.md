# TR_opti — Spécifications fonctionnelles

Version : novembre 2026 · Périmètre : BTCUSDT (contrat perpétuel USDT), unité de temps 15 minutes.

## 1. Objet

TR_opti est un **scanner d'analyse de marché** pour le Bitcoin. Toutes les 15 minutes il
analyse BTCUSDT sur plusieurs unités de temps, calcule un **score pondéré sur 100** et, si le
score est suffisant, publie une **proposition d'entrée** complète (sens, entrée, stop, objectif,
rapport gain/risque, score, palier, taux de réussite historique du palier).

Le système **ne passe aucun ordre**. Il n'est pas un conseil en investissement : il fournit une
analyse et un suivi honnête des résultats de ses propres propositions.

## 2. Utilisateurs et usages

| Utilisateur | Usage |
|---|---|
| Trader (propriétaire du projet) | Consulter la proposition en cours, son score et son % historique ; décider seul d'entrer ou non. |
| Analyste / mainteneur | Rejouer l'historique, mesurer la performance, ajuster les poids et la configuration. |

## 3. Cycle de fonctionnement

1. Un service de planification externe déclenche un scan **toutes les 15 minutes, à :00, :15, :30 et :45 UTC**
   (mêmes minutes en heure de Paris), juste après la clôture de chaque bougie 15 min.
2. Le scan récupère les bougies **1D, 4H, 1H et 15M** de BTCUSDT (données publiques Binance).
3. Il analyse le marché, évalue un setup **LONG** et un setup **SHORT**, garde le meilleur.
4. Il met à jour le statut des propositions existantes (déclenchée, gagnée, perdue, expirée…).
5. Il recalcule les statistiques et publie les données ; le dashboard se met à jour.

Les données affichées sont disponibles environ 1 minute après le quart d'heure (temps de démarrage
de l'exécution).

## 4. Analyse multi-unités de temps

| Unité | Rôle |
|---|---|
| 1D | Biais de fond (contexte). |
| 4H | Régime de marché (tendance haussière / baissière / range) et tendance principale. |
| 1H | Confirmation de direction. |
| 15M | Signal : structure, liquidité, momentum, volume, niveaux d'entrée / stop / objectif. |

Critères évalués (chacun rapporte des points) :

| Famille | Critères |
|---|---|
| Tendance (HTF) | Alignement 4H et 1H (empilement EMA 20/50/200 + ADX). |
| Structure | Cassure de structure (BOS) ou changement de caractère (CHoCH) récent, BOS additionnel, déplacement (bougie impulsive ≥ 1,5 ATR). |
| Liquidité (SMC) | Balayage de liquidité (sweep), Fair Value Gap, Order Block (frais ou déjà testés). |
| Momentum | RSI dans la zone du sens, ADX et directionnels (DI) alignés ou tendance naissante. |
| Volume | Volume relatif (vs moyenne des 20 dernières bougies). |
| Volatilité | ATR % dans la plage exploitable. |
| Gain / risque | Rapport gain/risque de la proposition. |
| Contexte | Biais 1D aligné, séance (Asie / Londres / New York). |

## 5. Système de propositions pondérées (mode actif)

### 5.1 Score
- Chaque famille rapporte des points (barème au §5.4). Le total est ramené sur **100**.
- Le score **classe la qualité de la confluence** ; ce n'est pas une probabilité. La probabilité
  affichée est le **taux de réussite historique mesuré** du palier (voir §7).

### 5.2 Paliers

| Palier | Score |
|---|---|
| A+ | ≥ 90 |
| A | ≥ 85 |
| B+ | ≥ 80 |
| B | ≥ 70 |
| C | ≥ 50 |
| (rien publié) | < 50 |

### 5.3 Règles de publication
- **RG-01** — Une proposition n'est publiée que si son score est ≥ 50.
- **RG-02** — Le sens publié est celui qui obtient le **meilleur score** (LONG ou SHORT).
- **RG-03** — **Une seule proposition active à la fois** pour BTC (en attente d'entrée ou en cours).
  Les résultats restent ainsi indépendants les uns des autres.
- **RG-04** — Délai minimum de 60 minutes entre deux propositions du même sens ; un setup identique
  n'est pas republié.
- **RG-05** — Blocages stricts conservés : données incomplètes, volatilité hors plage
  (ATR % < 0,10 % ou > 3 %), stop hors limites, **coût de transaction > 0,25 R**.
- **RG-06** — Objectif : le niveau de structure le plus proche offrant un gain/risque ≥ 1,5 ;
  à défaut, objectif fixe à **2 R**.

### 5.4 Barème des points (avant mise à l'échelle)

| Famille | Max | Détail |
|---|---|---|
| Tendance 4H/1H | 20 | 4H fortement aligné 10 / aligné 6 ; 1H fortement aligné 10 / aligné 6 / neutre 2 |
| Structure | 20 | Événement BOS/CHoCH ≤ 6 bougies 10 / plus ancien 6 ; BOS additionnel +5 ; déplacement +5 |
| Liquidité SMC | 20 | Sweep 10 ; FVG +5 (2,5 si déjà testé) ; Order Block +5 (2,5 si déjà testé) |
| Momentum | 15 | RSI dans la zone 8 (dégressif hors zone) ; ADX ≥ seuil et DI alignés 7 |
| Volume | 10 | Linéaire de 1,0× à 2,0× le volume moyen |
| Volatilité | 5 | ATR % dans la plage |
| Gain / risque | 10 | ≥ 3 R : 10 ; entre 1,5 et 3 R : 5 → 10 |
| Contexte | 10 | Biais 1D aligné 5 (neutre 2) ; séance Londres/New York 5 (Asie 2) |

Total maximal 110, ramené à 100. **Ce barème est en cours de calibration** (voir §10).

## 6. Contenu d'une proposition

| Champ | Description |
|---|---|
| Sens | LONG ou SHORT. |
| Déclencheur (trigger) | Prix d'entrée : au-dessus du plus haut de la bougie de confirmation (+0,1 ATR) pour un LONG, miroir pour un SHORT. L'entrée n'est valide que si le prix traverse ce niveau. |
| Zone d'entrée | Déclencheur ± 0,5 ATR. |
| Stop (SL) | Sous le point d'invalidation (plus bas récent / niveau balayé) − 0,5 ATR (miroir en SHORT). |
| Objectif (TP) | Niveau de structure le plus proche (≥ 1,5 R) ou 2 R fixe. |
| Gain / risque | (TP − entrée) / (entrée − SL). |
| Score, palier | Voir §5. |
| % historique | Taux de réussite constaté du palier et nombre de trades (n). |
| Contexte | Biais 1D/4H/1H, régime, séance, RSI, ADX, volume, funding (information). |

## 7. Cycle de vie et mesure des résultats

| Statut | Signification |
|---|---|
| En attente d'entrée | Proposition publiée, déclencheur pas encore touché. |
| Expirée | Déclencheur non touché en **3 heures** (12 bougies). |
| Entrée déclenchée | Le prix a traversé le déclencheur (en cas de gap, entrée au prix d'ouverture). |
| Gagnée (TP) | Objectif atteint avant le stop. |
| Perdue (SL) | Stop atteint avant l'objectif. |
| Indéterminée | Objectif et stop touchés dans la même bougie sans pouvoir les départager. |
| Temps maximal atteint | Trade clôturé au prix du marché après **4 heures** (16 bougies). |

Indicateurs de performance :
- **Taux de réussite** = gagnées / (gagnées + perdues). Les autres statuts sont exclus du dénominateur.
- Résultat en **R** (multiples du risque), brut et **net de frais** (0,05 % par côté + funding).
- Profit factor, espérance, séries, drawdown, simulation Monte Carlo.
- **Calibration par palier** : taux de réussite par palier avec intervalle de confiance (Wilson 95 %).
  C'est ce taux qui est affiché comme « % historique » sur la carte de proposition.

## 8. Historique et replay

- Toutes les propositions sont conservées dans l'**historique** avec leur résultat.
- Le **replay** rejoue le passé bougie par bougie, sans jamais utiliser de données futures, et produit
  les propositions qui auraient été publiées. Les propositions terminées d'un replay peuvent être
  ajoutées à l'historique ; elles sont marquées **« replay »** et précèdent toujours la première
  proposition en direct.
- Le replay fournit aussi l'**entonnoir** : nombre d'évaluations, motifs de non-publication.

## 9. Dashboard

Deux vues :

**Analyse BTC**
- État du système : dernier scan, prochain créneau, santé des données et de l'API.
- Dernière analyse BTC : graphique 15 min, biais, régime, indicateurs, niveaux.
- Propositions actives : cartes avec sens, prix, déclencheur, zone, TP, SL, gain/risque, score, palier,
  % historique, éléments de structure.
- Propositions récentes, entonnoir de rejet, performance (taux de réussite, profit factor, espérance,
  Monte Carlo, calibration par palier), méthodologie, journal du système.

**Historique des signaux**
- Tableau filtrable (période, sens, palier, résultat, recherche) avec entrée, TP, SL, gain/risque,
  statut, résultat en R et durée ; marqueur « replay » ; export CSV.

Les heures sont affichées en heure de Paris ; les créneaux de scan sont calculés en UTC.

## 10. Calibration (en cours)

Constat du replay 30 jours (octobre–novembre 2026, barème actuel) : 139 propositions,
9 gagnées / 43 perdues (17 %), 50 clôturées par la limite de 4 h, 37 non déclenchées ; **résultat négatif**
et **score non prédictif**. La calibration vise à :
1. Construire un jeu de données (chaque bougie, chaque sens, valeur brute de chaque critère, résultat
   d'un trade standard).
2. Mesurer l'apport réel de chaque critère sur le taux de réussite.
3. Fixer les poids à partir de ces mesures (pas de 5 points), régler stop / objectif / durée.
4. Valider sur une période non utilisée (ajustement sur 3 semaines, test sur la suivante, glissant).
5. Afficher alors un % issu de cette validation, recalibré chaque mois.

## 11. Mode strict (alternative)

Le mode d'origine reste disponible par configuration : tous les critères SMC deviennent obligatoires
(sweep → BOS/CHoCH → déplacement → FVG/OB), score ≥ 80 et gain/risque ≥ 2,5. Il ne produit quasiment
aucun signal sur BTC dans les conditions actuelles.

## 12. Limites et avertissements

- Analyse uniquement, aucune exécution d'ordre, aucune garantie de résultat.
- Les performances passées ne préjugent pas des performances futures ; les petits échantillons ne
  sont pas significatifs (le nombre de trades est toujours affiché).
- Données : Binance en priorité ; en cas de blocage, bascule possible vers un miroir ou une autre
  plateforme (indiqué dans l'état du système).
