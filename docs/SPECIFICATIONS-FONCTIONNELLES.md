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

Une seule page, consacrée à la Tendance 4H (voir § 13). Les résultats du scanner BTC 15 min restent
publiés dans `data/` mais ne sont plus affichés.

- État du système : santé du moteur (OK / dégradé / en retard après 45 min / hors ligne après 3 h),
  dernier passage, prochain passage, prochaine clôture 4H.
- Capital fictif valorisé, positions ouvertes, nombre de trades, pire baisse.
- Une carte par crypto avec un graphique des 60 dernières bougies 4H (seuils d'achat / de vente, ou
  entrée, stop et sortie suiveuse si un trade est ouvert) : si un trade est ouvert, entrée, stop loss, sortie suiveuse (pas de TP fixe),
  risque en € et en %, taille, levier, résultat latent et financement ; si une entrée est en attente,
  le sens et le risque prévus ; sinon les niveaux de cassure (achat / vente, écart en %) et une phrase
  expliquant pourquoi aucun trade n'est pris (pas de cassure, filtre tendance, volume, financement,
  dernière cassure écartée), avec le stop, la taille et le levier qu'aurait un trade immédiat.
- Historique des trades ; cassures prises ou écartées avec le motif et leur résultat « si on était
  entré » (trade fantôme), avec la synthèse prises / écartées ; règles en section repliée.

Les heures sont affichées en heure de Paris.

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

## 13. Tendance 4H (simulation)

Stratégie de suivi de tendance retenue après les études de recherche (seule approche positive sur
2019-2026 après frais, glissement et financement). Elle tourne **en simulation** : capital fictif de
500 €, aucun ordre réel. Période de test prévue : 1 mois, puis comparaison avec le backtest.

- **Cryptos** : BTC, ETH, XRP, BNB, SOL (contrats perpétuels USDT).
- **Signal** : une bougie 4H clôture au-dessus du plus haut (achat) ou sous le plus bas (vente) des
  20 bougies précédentes.
- **Filtres** : dans le sens de BTC par rapport à sa moyenne mobile 200 en 4H ; volume de la bougie
  de signal > 2 × la moyenne des 20 précédentes ; pas d'entrée si le financement dépasse 0,03 % par
  8 h dans le sens du trade.
- **Entrée** à l'ouverture de la bougie suivante ; **stop initial** à 2 × ATR(14).
- **Sortie** : clôture 4H sous le plus bas (achat) ou au-dessus du plus haut (vente) des 10 dernières
  bougies, ou stop touché (au prix d'ouverture si le marché saute le stop).
- **Taille** : 1 % du capital réalisé perdu si le stop est touché ; une position par crypto,
  5 au maximum ; exposition totale ≤ 5 × le capital.
- **Coûts simulés** : frais 0,05 % et glissement 0,025 % par ordre, financement à chaque
  règlement de 8 h (dernier taux publié).
- **Démarrage** sans reconstitution du passé ; si des scans manquent, les bougies 4H clôturées
  entre-temps sont traitées au scan suivant, sans doublon.
- Profil attendu (backtest) : environ 3 trades par semaine, environ 1/3 de trades gagnants, séries de
  pertes de 10 à 15 trades possibles, pire baisse historique d'environ −30 à −40 %.

## 14. Analyste IA (test, sans effet sur les trades)

Objectif : mesurer si une IA qui lit l'actualité apporte une information que nos règles ne calculent
pas. Son avis est consigné et noté, il ne déclenche ni ne bloque aucun trade.

- **Quand** : une fois par clôture 4H (au premier passage qui suit), jamais plus de 90 min après la
  clôture.
- **Ce qu'elle reçoit** : nos faits (cours, variation 24 h / 7 j, ATR, moyennes 20 / 50, RSI, plus haut / bas
  10 jours, canal 20 bougies, volume, financement, tendance BTC, 12 dernières bougies 4H, cassures de
  cette clôture, sa propre position ouverte) mais **pas** la décision de nos filtres.
- **Ce qu'elle fait** : recherche Google (macro et calendrier, dollar, taux, actions, flux des ETF et des
  plateformes, positions sur dérivés et options, liquidations, actualité de chaque crypto, régulation,
  sentiment) puis rend, par crypto, une direction à 24 h avec la probabilité de hausse, pour chaque
  cassure un GO / NO-GO avec la probabilité d'atteindre +2R, et **son propre trade** (achat, vente ou
  aucun, conviction, stop, objectif, durée maximale, raison).
- **Portefeuille IA** (fictif) : ses trades sont simulés avec les mêmes règles de risque et de coûts
  que le système (500 €, 1 % par trade, entrée à l'ouverture de la première bougie qui suit la réponse
  de l'IA — depuis le 10/11/2026, avant : ouverture suivant la clôture analysée —, sortie au stop, à l'objectif
  ou à la durée maximale, une position par crypto). Stop rejeté s'il est du mauvais côté ou hors
  0,5-5 ATR. Les deux capitaux sont comparés sur le tableau de bord.
- **Notation** : directions comparées à la clôture 24 h plus tard (taux de réussite, score de Brier,
  hasard = 0,25) ; GO / NO-GO comparés au résultat du trade fantôme de la même cassure, dans un
  tableau « nous : prise / écartée » × « IA : GO / NO-GO ».
- **Affichage** : avis sur chaque carte crypto, colonne « Avis IA » dans le tableau des cassures,
  section « Analyste IA » (état, note macro, scores, tableau qui avait raison, recherches et sources).
- Une conclusion demande plusieurs mois ; un mois ne donne qu'une tendance.

## 15. Second portefeuille : 4H + renfort 1H (simulation)

Portefeuille fictif indépendant, affiché dans l'onglet « 4H + renfort 1H » (l'état du système reste commun
en haut de page). La Tendance 4H (§ 13) et son analyste IA (§ 14) ne sont pas modifiés.

- **Livre 4H** : exactement les règles de la Tendance 4H, risque 1 % par trade.
- **Renfort 1H** : clôture 1H au-delà du plus haut / plus bas des 160 bougies 1H, mêmes 3 filtres
  (tendance BTC lue en 4H, volume, financement), **uniquement pendant une position 4H ouverte dans le
  même sens** ; stop 2 × ATR 1H, sortie suiveuse sur 40 bougies 1H, risque 0,5 % par trade.
- **Capital commun** de 500 € pour les deux livres, gains réinvestis, mêmes coûts.
- **Analyste IA dédié** (mêmes réglages que § 14) : analyse à chaque clôture 4H et à chaque clôture 1H
  où une cassure apparaît, avis GO / NO-GO sur les cassures 4H et 1H, direction à 24 h, et son propre
  portefeuille fictif suivi sur bougies 1H.
- Choix issus de la recherche : sur 2019-2026, le renfort 1H pendant une position 4H donne +0,87R par
  trade (+0,61R sur la période non utilisée pour le choix) ; pire baisse simulée d'environ −41 %.

## 16. Troisième portefeuille : 4H + 1H optimisé (simulation)

Portefeuille fictif indépendant (onglet « 4H + 1H optimisé »), démarré le 6 novembre 2026. Mêmes règles
que le § 15, avec trois réglages issus de la recherche (choisis sur 2019 → 10/2024, vérifiés sur 11/2024 →) :

- **Tendance BTC plus lente** : clôture 4H de BTC comparée à sa moyenne des 300 dernières bougies (au lieu de 200).
- **Au plus 3 positions dans le même sens**, 4H et 1H comptées ensemble (les 5 cryptos sont très corrélées).
- **Mise doublée** : 2 % du capital par trade 4H, 1 % par renfort 1H.
- Capital commun de 500 €, mêmes coûts ; pas d'analyste IA.
- Simulation 2019 → 11/2026 : 500 € → environ 549 000 € (pire baisse −39 %) contre 118 000 € (−41 %) pour le § 15 ;
  sur la seule période de vérification : 2 047 € contre 1 424 €.
