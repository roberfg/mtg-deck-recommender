"""Analiza el metajuego de varios formatos y recomienda mazos para comprar."""

from __future__ import annotations

import math
import re
import sys
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup


FORMAT_URLS = {
    "standard": "https://mtgdecks.net/Standard/metagame:last-30-days",
    "pioneer": "https://mtgdecks.net/Pioneer/metagame:last-30-days",
    "modern": "https://mtgdecks.net/Modern/metagame:last-30-days",
    "pauper": "https://mtgdecks.net/Pauper/metagame:last-30-days",
    "premodern": "https://mtgdecks.net/Premodern/metagame:last-30-days",
}
BEST_WEIGHTS = {
    "price_score": 0.30,
    "conversion_score": 0.35,
    "winrate_score": 0.30,
    "trend_score": 0.05,
}
CHEAP_WEIGHTS = {
    "price_score": 0.80,
    "conversion_score": 0.10,
    "winrate_score": 0.05,
    "trend_score": 0.05,
}
PROFILES = {"cheap": CHEAP_WEIGHTS, "best": BEST_WEIGHTS}
TIERS = {"A"}
MIX_INTRO = (
    "Criterio: si un mazo está entre la primera y la segunda recomendación de ambos perfiles, "
    "se elige ese mazo. Si varios coinciden, gana el de menor suma de posiciones y, en empate, "
    "el de precio Paper más bajo. Si no hay coincidencia, se elige el mazo con menor arrepentimiento "
    "máximo: la diferencia entre el IVC del mejor mazo de cada perfil y el IVC del candidato.\n\n"
)


def parse_number(value: str | None) -> float | None:
    """Convierte un porcentaje, multiplicador o precio en un número."""
    if not value:
        return None
    normalized = value.strip().replace(",", ".")
    if normalized in {"", "-", "—", "NA"}:
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", normalized)
    return float(match.group()) if match else None


def parse_price(cell: Any, price_class: str) -> float | None:
    """Extrae el precio de una modalidad concreta del HTML."""
    price = cell.select_one(f"span.{price_class}")
    return parse_number(price.get_text(" ", strip=True) if price else None)


def parse_decks(html: str) -> pd.DataFrame:
    """Extrae todas las filas del dataframe publicado por MTGDecks."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table#allArchetypes")
    if table is None:
        raise ValueError("No se encontró la tabla de arquetipos de MTGDecks.")

    rows: list[dict[str, Any]] = []
    for row in table.select("tbody tr"):
        cells = row.select("td")
        if len(cells) < 9:
            continue

        name_node = row.select_one("strong[name]")
        meta_node = row.select_one("td.meta .meta-share.hidden-xs")
        if meta_node is None:
            meta_node = row.select_one("td.meta .meta-share")
        metric_text = [cell.get_text(" ", strip=True) for cell in cells]
        tier = metric_text[4] or None

        rows.append(
            {
                "name": name_node.get("name") if name_node else metric_text[1],
                "meta_share_pct": parse_number(meta_node.get_text(strip=True) if meta_node else None),
                "trend_pct": parse_number(metric_text[3]),
                "tier": tier,
                "winrate_pct": parse_number(metric_text[5]),
                "top25_conversion": parse_number(metric_text[6]),
                "decks": parse_number(metric_text[7]),
                "paper_price_usd": parse_price(cells[8], "paper"),
                "mtgo_price_usd": parse_price(cells[8], "mtgo"),
            }
        )

    if not rows:
        raise ValueError("La tabla no contiene filas de mazos.")
    return pd.DataFrame(rows).convert_dtypes()


def normalize_series(series: pd.Series, higher_is_better: bool = True) -> pd.Series:
    """Normaliza una métrica entre 1 y 10 mediante interpolación lineal."""
    minimum = series.min()
    maximum = series.max()
    if minimum == maximum:
        return pd.Series(5.0, index=series.index)
    ratio = (series - minimum) / (maximum - minimum)
    if not higher_is_better:
        ratio = 1 - ratio
    return 1 + ratio * 9


def relative_price_score(prices: pd.Series) -> tuple[pd.Series, float]:
    """Puntúa el precio respecto a la mediana: mediana=5, con límites 1..10."""
    median = float(prices.median())
    scores = 5 - 4 * prices.map(lambda price: math.log2(price / median))
    return scores.clip(lower=1, upper=10), median


def calculate_ivc(
    dataframe: pd.DataFrame,
    weights: dict[str, float],
    tiers: set[str],
) -> tuple[pd.DataFrame, float]:
    """Filtra los datos válidos y calcula las puntuaciones y el IVC."""
    required = ["winrate_pct", "top25_conversion", "paper_price_usd"]
    valid_mask = (
        (dataframe["name"].str.lower() != "rogue")
        & dataframe[required].notna().all(axis=1)
        & (dataframe["paper_price_usd"] > 0)
        & dataframe["tier"].isin(tiers)
    )
    valid = dataframe[valid_mask].copy()
    if valid.empty:
        raise ValueError("No hay mazos con precio Paper, winrate y conversión válidos.")

    valid["price_score"], median_price = relative_price_score(valid["paper_price_usd"])
    valid["conversion_score"] = normalize_series(valid["top25_conversion"])
    valid["winrate_score"] = normalize_series(valid["winrate_pct"])
    valid["trend_score"] = normalize_series(valid["trend_pct"].fillna(0))
    valid["ivc"] = sum(valid[column] * weight for column, weight in weights.items())
    valid = valid.sort_values(["ivc", "paper_price_usd"], ascending=[False, True]).reset_index(drop=True)
    return valid, median_price


def select_recommendation(dataframe: pd.DataFrame) -> pd.Series:
    """Elige siempre el mazo con el IVC más alto."""
    return dataframe.sort_values(["ivc", "paper_price_usd"], ascending=[False, True]).iloc[0]


def select_second_recommendation(dataframe: pd.DataFrame, first: pd.Series) -> pd.Series | None:
    """Elige la siguiente alternativa del ranking, excluyendo la primera."""
    alternatives = dataframe[dataframe["name"] != first["name"]]
    if alternatives.empty:
        return None
    return alternatives.sort_values(["ivc", "paper_price_usd"], ascending=[False, True]).iloc[0]


def top_two_names(scored: pd.DataFrame) -> list[str]:
    """Devuelve la primera y segunda recomendación de un perfil."""
    first = select_recommendation(scored)
    second = select_second_recommendation(scored, first)
    names = [first["name"]]
    if second is not None:
        names.append(second["name"])
    return names


def find_deck(dataframe: pd.DataFrame, name: str) -> pd.Series:
    """Busca la fila de un mazo por nombre."""
    return dataframe[dataframe["name"] == name].iloc[0]


def rank_of(dataframe: pd.DataFrame, name: str) -> int:
    """Devuelve la posición de un mazo en el ranking ordenado por IVC."""
    return int(dataframe.index[dataframe["name"] == name][0]) + 1


def choose_mix_deck(cheap: pd.DataFrame, best: pd.DataFrame) -> tuple[str, str]:
    """Elige un mazo combinando las recomendaciones de ambos perfiles."""
    cheap_pool = top_two_names(cheap)
    best_pool = top_two_names(best)
    overlap = [name for name in cheap_pool if name in best_pool]
    if overlap:
        name = min(
            overlap,
            key=lambda deck: (
                rank_of(cheap, deck) + rank_of(best, deck),
                find_deck(cheap, deck)["paper_price_usd"],
            ),
        )
        return name, "Recomendado en ambos perfiles"

    best_ivc = {"cheap": cheap["ivc"].max(), "best": best["ivc"].max()}

    def max_regret(deck: str) -> float:
        return max(
            best_ivc["cheap"] - find_deck(cheap, deck)["ivc"],
            best_ivc["best"] - find_deck(best, deck)["ivc"],
        )

    name = min(
        cheap_pool + best_pool,
        key=lambda deck: (max_regret(deck), find_deck(cheap, deck)["paper_price_usd"]),
    )
    return name, "Sin coincidencia: menor arrepentimiento máximo"


def dataframe_markdown(dataframe: pd.DataFrame, columns: list[str]) -> str:
    """Convierte columnas del dataframe a una tabla Markdown legible."""
    display = dataframe[columns].astype(object).copy()
    display = display.where(display.notna(), "—")
    return display.to_markdown(index=False, floatfmt=".2f")


def build_markdown_report(
    format_name: str,
    raw: pd.DataFrame,
    scored: pd.DataFrame,
    median_price: float,
    weights: dict[str, float],
) -> str:
    """Genera el informe Markdown de un formato y un perfil."""
    recommendation = select_recommendation(scored)
    second_recommendation = select_second_recommendation(scored, recommendation)
    weight_summary = (
        f"precio {weights['price_score']:.0%}, "
        f"conversión Top 25% {weights['conversion_score']:.0%}, "
        f"winrate {weights['winrate_score']:.0%}, "
        f"trend {weights['trend_score']:.0%}"
    )
    second_name = second_recommendation["name"] if second_recommendation is not None else "No disponible"
    second_details = ""
    if second_recommendation is not None:
        second_details = f"""- IVC: **{second_recommendation['ivc']:.2f}**
- Precio Paper: **${second_recommendation['paper_price_usd']:.2f}**
- Winrate: **{second_recommendation['winrate_pct']:.0f}%**
- Conversión Top 25%: **{second_recommendation['top25_conversion']:.2f}x**
- Trend: **{second_recommendation['trend_pct']:+.2f}%**
"""
    warning = ""
    if recommendation["decks"] < 10:
        warning = (
            f"\n> Advertencia: este arquetipo solo tiene {int(recommendation['decks'])} listas "
            "registradas; su IVC es menos confiable.\n"
        )

    return f"""## {format_name.title()}

### Resumen

- Mazos extraídos: {len(raw)}
- Mazos válidos incluidos: {len(scored)}
- Precio Paper mediano: ${median_price:.2f}
- Ponderación: {weight_summary}

### Recomendación

**{recommendation['name']}**

- IVC: **{recommendation['ivc']:.2f}**
- Precio Paper: **${recommendation['paper_price_usd']:.2f}**
- Winrate: **{recommendation['winrate_pct']:.0f}%**
- Conversión Top 25%: **{recommendation['top25_conversion']:.2f}x**
- Trend: **{recommendation['trend_pct']:+.2f}%**
{warning}
### Segunda recomendación

**{second_name}**
{second_details}
"""


def build_mix_markdown(
    format_name: str,
    raw: pd.DataFrame,
    cheap: pd.DataFrame,
    best: pd.DataFrame,
    median_price: float,
    mix_name: str,
    mix_reason: str,
) -> str:
    """Genera la sección de mezcla de un formato."""
    chosen_cheap = find_deck(cheap, mix_name)
    chosen_best = find_deck(best, mix_name)
    warning = ""
    if chosen_cheap["decks"] < 10:
        warning = (
            f"\n> Advertencia: este arquetipo solo tiene {int(chosen_cheap['decks'])} listas "
            "registradas; su IVC es menos confiable.\n"
        )

    candidates = []
    for name in dict.fromkeys(top_two_names(cheap) + top_two_names(best)):
        candidate_cheap = find_deck(cheap, name)
        candidate_best = find_deck(best, name)
        candidates.append(
            {
                "Mazo": name,
                "Posición cheap": rank_of(cheap, name),
                "Posición best": rank_of(best, name),
                "IVC cheap": candidate_cheap["ivc"],
                "IVC best": candidate_best["ivc"],
                "Precio Paper": candidate_cheap["paper_price_usd"],
            }
        )
    candidate_table = dataframe_markdown(pd.DataFrame(candidates), list(candidates[0].keys()))

    return f"""## {format_name.title()}

### Resumen

- Mazos extraídos: {len(raw)}
- Mazos válidos incluidos: {len(cheap)}
- Precio Paper mediano: ${median_price:.2f}

### Recomendación final

**{mix_name}**

- Criterio: {mix_reason}
- Posición cheap: **#{rank_of(cheap, mix_name)}** (IVC {chosen_cheap['ivc']:.2f})
- Posición best: **#{rank_of(best, mix_name)}** (IVC {chosen_best['ivc']:.2f})
- Precio Paper: **${chosen_cheap['paper_price_usd']:.2f}**
- Winrate: **{chosen_cheap['winrate_pct']:.0f}%**
- Conversión Top 25%: **{chosen_cheap['top25_conversion']:.2f}x**
- Trend: **{chosen_cheap['trend_pct']:+.2f}%**
{warning}
### Candidatos

{candidate_table}

"""


def fetch_html(url: str) -> str:
    """Descarga la página con una identificación básica del cliente."""
    response = requests.get(
        url,
        headers={"User-Agent": "deck-recommender/1.0"},
        timeout=30,
    )
    response.raise_for_status()
    return response.text


def log(format_name: str, message: str) -> None:
    """Muestra un mensaje de progreso asociado a un formato."""
    print(f"[{format_name.title()}] {message}")


def warn_low_sample(format_name: str, label: str, deck: pd.Series) -> None:
    """Avisa en consola cuando un mazo recomendado tiene pocas listas."""
    if deck["decks"] < 10:
        log(
            format_name,
            f"ADVERTENCIA ({label}): {deck['name']} solo tiene {int(deck['decks'])} listas; "
            "su IVC es menos confiable.",
        )


def write_report(path: Path, title: str, body: str) -> None:
    """Escribe un informe Markdown con cabecera común."""
    with path.open("w", encoding="utf-8") as report_file:
        report_file.write(f"# {title}\n\nPeriodo: últimos 30 días\n\n{body}")


def process_format(
    format_name: str,
    url: str,
    export_directory: Path,
) -> tuple[dict[str, str], str]:
    """Procesa un formato completo y devuelve sus informes Markdown por perfil y de mezcla."""
    log(format_name, "Descargando datos de MTGDecks...")
    raw = parse_decks(fetch_html(url))
    raw.insert(0, "format", format_name)
    raw.to_csv(export_directory / f"deck_data_{format_name}.csv", index=False)

    log(format_name, "Analizando mazos de los últimos 30 días...")
    scored_by_profile: dict[str, pd.DataFrame] = {}
    markdown_by_profile: dict[str, str] = {}
    median_price = 0.0
    for profile, weights in PROFILES.items():
        scored, median_price = calculate_ivc(raw, weights, TIERS)
        scored.to_csv(export_directory / f"deck_analysis_{format_name}_{profile}.csv", index=False)
        warn_low_sample(format_name, profile, select_recommendation(scored))
        markdown_by_profile[profile] = build_markdown_report(format_name, raw, scored, median_price, weights)
        scored_by_profile[profile] = scored

    cheap = scored_by_profile["cheap"]
    best = scored_by_profile["best"]
    mix_name, mix_reason = choose_mix_deck(cheap, best)
    warn_low_sample(format_name, "final", find_deck(cheap, mix_name))
    mix_markdown = build_mix_markdown(format_name, raw, cheap, best, median_price, mix_name, mix_reason)
    log(format_name, f"Completado: {len(raw)} mazos extraídos, {len(cheap)} válidos.")
    return markdown_by_profile, mix_markdown


def main() -> int:
    export_directory = Path("exports")
    export_directory.mkdir(exist_ok=True)
    reports: dict[str, list[str]] = {profile: [] for profile in PROFILES}
    mix_sections: list[str] = []
    failed_formats: list[str] = []

    for format_name, url in FORMAT_URLS.items():
        try:
            markdown_by_profile, mix_markdown = process_format(format_name, url, export_directory)
        except Exception as error:
            failed_formats.append(format_name)
            print(f"[{format_name.title()}] ERROR: {error}. Formato omitido.")
            continue
        for profile, markdown in markdown_by_profile.items():
            reports[profile].append(markdown)
        mix_sections.append(mix_markdown)

    if not mix_sections:
        print("\nERROR: ningún formato se completó; no se generaron informes.")
        return 1

    print("\nGenerando informes...")
    for profile in PROFILES:
        write_report(
            export_directory / f"deck_recommendations_{profile}.md",
            "Recomendaciones de mazos",
            "\n".join(reports[profile]),
        )
    write_report(
        export_directory / "deck_recommendations_final.md",
        "Recomendación final",
        MIX_INTRO + "\n".join(mix_sections),
    )

    completed = len(mix_sections)
    total = len(FORMAT_URLS)
    print(f"\nResumen: {completed} de {total} formatos completados.")
    if failed_formats:
        names = ", ".join(name.title() for name in failed_formats)
        print(f"Formatos con error: {names}.")
        print(f"Informes guardados en {export_directory}/ con los formatos completados.")
        return 1
    print(f"Ejecución completada correctamente. Informes guardados en {export_directory}/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
