# Sistema di Similarità Sketch Parametrica (u, v)

## Introduzione

Questo documento descrive il sistema di confronto degli sketch 2D basato su un sistema di coordinate parametriche normalizzate (u, v), indipendente dal sistema di riferimento globale del CAD.

## Problema

Nel confronto di modelli CAD creati da persone diverse, gli sketch 2D potrebbero essere posizionati su piani di riferimento diversi o con orientamenti diversi, anche se rappresentano la stessa geometria. Usando il sistema di coordinate assoluto, due sketch identici ma orientati diversamente risulterebbero completamente diversi.

## Soluzione: Frame Parametrico (u, v)

Per ogni sketch 2D, definiamo un **frame parametrico locale** composto da:

1. **Punto O (centroide)**: il baricentro geometrico pesato di tutte le geometrie dello sketch
2. **Vettori u e v**: assi principali ortogonali (da PCA sui punti caratteristici)
3. **Pesi (weight_u, weight_v)**: vettore normalizzato delle proiezioni totali delle geometrie lungo u e v

### Calcolo del Baricentro Geometrico

Il baricentro è calcolato come media dei baricentri delle singole geometrie:
- **Linea**: baricentro = punto medio
- **Cerchio**: baricentro = centro
- **Arco**: baricentro = centro
- **Ellisse**: baricentro = centro

### Calcolo degli Assi Principali (PCA)

Usiamo l'Analisi delle Componenti Principali sui punti caratteristici (start, end, center):
1. Calcola il centroide
2. Centra i punti rispetto al centroide
3. Calcola la matrice di covarianza 2x2
4. Trova autovalori e autovettori
5. Il primo autovettore (λ₁ maggiore) diventa **u**
6. Il secondo autovettore diventa **v** (ortogonale a u)

### Calcolo dei Pesi Normalizzati (weight_u, weight_v)

I pesi rappresentano quanto lo sketch "si estende" lungo ciascun asse, normalizzati come vettore unitario:

1. Per ogni geometria, calcola la proiezione sugli assi:
   - **Linea**: proiezione = |vettore_linea · asse|
   - **Cerchio/Arco**: proiezione = diametro (uguale su entrambi gli assi)
   - **Altro**: usa bounding box proiettato

2. Somma tutte le proiezioni: `total_u`, `total_v`

3. Normalizza: `weight = (total_u, total_v) / ||(total_u, total_v)||`

**Esempio - Cerchio di raggio 1:**
- Proiezione su u = diametro = 2
- Proiezione su v = diametro = 2
- Normalizzato: (2, 2) / √8 = (√2/2, √2/2) ≈ (0.707, 0.707)

**Esempio - Rettangolo 100x50:**
- 4 linee con proiezioni totali: proj_u = 200, proj_v = 100
- Normalizzato: (200, 100) / √50000 ≈ (0.894, 0.447)

## Algoritmo di Matching

Per confrontare due modelli:

1. **Selezione**: prendi `min(num_sketch_modello1, num_sketch_modello2)` come numero di coppie
2. **Matching greedy**: per ogni sketch del modello con meno sketch, trova il match migliore nell'altro modello
3. **Calcolo similarità coppia**: combina:
   - **Similarità orientamento** (25%): `(|u₁·u₂| + |v₁·v₂|) / 2`
   - **Similarità pesi** (35%): prodotto scalare `weight₁ · weight₂` (entrambi normalizzati)
   - **Similarità geometrie** (40%): Jaccard su tipi + conteggi + vincoli

## Formula di Similarità Sketch Parametrica

La similarità finale tra due modelli per il criterio sketch parametrico è:

```
similarity = 0.80 * media(sim_coppie) + 0.20 * (min_sketches / max_sketches)
```

Dove:
- `media(sim_coppie)` è la media delle similarità di tutte le coppie matchate
- La penalità `min/max` penalizza modelli con numero di sketch molto diverso

## Interpretazione dei Pesi

| Forma dello Sketch | weight_u | weight_v | Interpretazione |
|-------------------|----------|----------|-----------------|
| Cerchio perfetto | 0.707 | 0.707 | Simmetrico |
| Quadrato | 0.707 | 0.707 | Simmetrico |
| Rettangolo 2:1 | 0.894 | 0.447 | Allungato in u |
| Linea orizzontale | 1.0 | 0.0 | Solo estensione in u |

## Vantaggi

1. **Invarianza al sistema di riferimento**: due sketch identici ma su piani diversi avranno alta similarità
2. **Invarianza alla posizione**: il baricentro è locale, non globale
3. **Cattura la forma**: i pesi normalizzati rappresentano la "forma" dello sketch
4. **Confronto significativo**: sketch con forme simili avranno pesi simili

## Strutture Dati

### SketchParametricFrame

```python
@dataclass
class SketchParametricFrame:
    centroid: Tuple[float, float]     # Baricentro geometrico
    axis_u: Tuple[float, float]       # Primo asse principale
    axis_v: Tuple[float, float]       # Secondo asse principale
    extent_u: float                   # weight_u (peso normalizzato)
    extent_v: float                   # weight_v (peso normalizzato)
    num_points: int                   # Numero punti usati
    is_valid: bool                    # Frame valido?
```

## Configurazione

Il peso può essere modificato in `config.json`:

```json
{
  "default_weights": {
    "sketch_parametric_similarity": 0.15
  }
}
```

O tramite l'interfaccia web/API.
