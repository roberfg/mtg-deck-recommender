# Contexto del Agente

## Propósito

Este proyecto recomienda mazos de Magic: The Gathering usando datos actuales de MTGDecks. El punto de entrada es `deck_recommender.py`.

## Fuente de datos

El script descarga en vivo el metajuego de los últimos 30 días para estos formatos:

- Standard
- Pioneer
- Modern
- Pauper
- Premodern

El precio utilizado para el cálculo es siempre `Paper`. El precio `MTGO` se conserva únicamente como dato informativo. La tabla esperada es `table#allArchetypes`.

## Ejecución y alcance

- `python deck_recommender.py` analiza los cinco formatos disponibles y genera en una sola ejecución los informes `cheap`, `best` y `final`.
- No hay flags. El perfil `cheap` aplica la ponderación de precio a todos los formatos, incluido Standard. El perfil `best` aplica la ponderación competitiva a todos los formatos, incluido Standard.
- Se consideran únicamente los mazos de tier A.
- La salida de terminal solo muestra mensajes de progreso por formato, advertencias y errores; no muestra tablas ni rankings de mazos, que se consultan en los informes Markdown y CSV.
- Si un formato falla, se muestra el error con su formato y se continúa con los demás. El código de salida es distinto de 0 si algún formato falla, y 1 si ninguno se completa.
- El periodo de análisis es siempre de los últimos 30 días.

## Reglas de filtrado

- Por defecto se incluye únicamente el tier A.
- `--tiers 1` incluye A, `--tiers 2` incluye A+B y `--tiers 3` incluye A+B+C.
- `--tiers all` no aplica filtro de tier e incluye todos los valores publicados.
- Se excluye el arquetipo `Rogue`.
- Se excluyen filas sin winrate, conversión Top 25% o precio `Paper` válido y positivo.
- Los mazos con pocas listas pueden aparecer, pero el informe y la consola advierten cuando la recomendación tiene menos de 10 listas.

## Cálculo del IVC

Para Pioneer, Modern, Pauper y Premodern:

```text
IVC = puntuación de precio * 0.30
    + puntuación de conversión Top 25% * 0.35
    + puntuación de winrate * 0.30
    + puntuación de trend * 0.05
```

Perfil `cheap` (todos los formatos):

```text
IVC = puntuación de precio * 0.80
    + puntuación de conversión Top 25% * 0.10
    + puntuación de winrate * 0.05
    + puntuación de trend * 0.05
```

Perfil `best` (todos los formatos):

```text
IVC = puntuación de precio * 0.30
    + puntuación de conversión Top 25% * 0.35
    + puntuación de winrate * 0.30
    + puntuación de trend * 0.05
```

Todas las métricas se normalizan entre 1 y 10. La puntuación del precio usa la mediana de los precios `Paper` válidos, no la media aritmética. La mediana recibe 5 puntos, los precios más bajos obtienen mejor puntuación y el resultado queda limitado entre 1 y 10.

La primera recomendación siempre es el mazo con el IVC más alto. Los empates se resuelven con el precio `Paper` más bajo. La segunda recomendación es el siguiente mazo distinto del ranking y se muestra con sus métricas principales.

## Recomendación final

Para cada formato se toman la primera y segunda recomendación de cada perfil (hasta cuatro mazos):

- Si un mazo aparece en ambos perfiles, se elige ese. Si coinciden varios, gana el de menor suma de posiciones en ambos rankings y, en empate, el de precio `Paper` más bajo.
- Si no hay coincidencia, se elige el mazo con menor arrepentimiento máximo. El arrepentimiento de un candidato en un perfil es el IVC máximo de ese perfil menos el IVC del candidato; se toma el máximo entre ambos perfiles y gana el mínimo.
- Los IVC de `cheap` y `best` no se comparan directamente porque usan ponderaciones distintas.

## Archivos generados

- `exports/deck_data_<format>.csv`: todas las filas extraídas, con una columna `format` añadida.
- `exports/deck_analysis_<format>_cheap.csv` y `exports/deck_analysis_<format>_best.csv`: filas válidas con puntuaciones e IVC de cada perfil.
- `exports/deck_recommendations_cheap.md`: informe combinado del perfil `cheap`, con el resumen y las recomendaciones de cada formato.
- `exports/deck_recommendations_best.md`: informe combinado del perfil `best`, con el mismo contenido.
- `exports/deck_recommendations_final.md`: informe combinado con la recomendación final de cada formato y los candidatos.

Los archivos generados están excluidos mediante `.gitignore`. Las dependencias están en `requirements.txt` y son `pandas`, `requests`, `beautifulsoup4` y `tabulate`.
