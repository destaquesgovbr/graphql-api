#!/usr/bin/env python3
"""Exporta o SDL do schema GraphQL para artefatos de documentacao.

Gera dois arquivos em `docs/reference/`:
  - `schema.graphql` — SDL bruto (fonte canonica, util para tooling/codegen).
  - `schema.md`      — SDL embrulhado em bloco ```graphql para o MkDocs renderizar.

Ambos sao GERADOS — nao editar a mao. Rode `make docs-schema` apos qualquer
mudanca no schema (tipos, queries, mutations, subscriptions) para manter a
referencia em sincronia com o codigo.

O import e barato: o schema Strawberry e code-first e nao abre conexoes com
datasources no import (isso so acontece no lifespan da app). Logo este script
roda sem `.env.local`, sem Postgres/Firestore/Typesense.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Permite rodar como `python scripts/export_schema.py` sem instalar o pacote.
_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from strawberry.printer import print_schema  # noqa: E402

from graphql_api.schema import schema  # noqa: E402

_REFERENCE_DIR = Path(__file__).resolve().parent.parent / "docs" / "reference"

_HEADER = (
    "<!-- GERADO AUTOMATICAMENTE por scripts/export_schema.py — NÃO EDITAR À MÃO. -->\n"
    "<!-- Rode `make docs-schema` para regenerar a partir do schema Strawberry. -->\n\n"
    "# Referência do schema (SDL)\n\n"
    "SDL completo do schema GraphQL, gerado a partir do código "
    "(`graphql_api.schema:schema`). O scalar usado para identificadores é "
    "`String` — **não existe** o scalar `ID` neste schema.\n\n"
    "!!! tip \"Playground interativo (GraphiQL)\"\n"
    "    Em vez de copiar o SDL, explore o schema ao vivo no **GraphiQL**, "
    "servido pela própria API em `GET /graphql`: introspecção, autocomplete e "
    "execução de queries no browser. Ver [Exemplos › Playground](../exemplos.md#playground-graphiql).\n\n"
)


def main() -> int:
    sdl = print_schema(schema)
    if not sdl.endswith("\n"):
        sdl += "\n"

    _REFERENCE_DIR.mkdir(parents=True, exist_ok=True)

    graphql_path = _REFERENCE_DIR / "schema.graphql"
    graphql_path.write_text(sdl, encoding="utf-8")

    md_path = _REFERENCE_DIR / "schema.md"
    md_path.write_text(f"{_HEADER}```graphql\n{sdl}```\n", encoding="utf-8")

    print(f"✓ {graphql_path.relative_to(_REFERENCE_DIR.parent.parent)} ({len(sdl.splitlines())} linhas)")
    print(f"✓ {md_path.relative_to(_REFERENCE_DIR.parent.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
