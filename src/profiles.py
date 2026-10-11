"""Carregamento e renderização de perfis de personalidade (--profile).

Um perfil é um arquivo YAML que descreve o comportamento da IA:

    profile_name: "Estrategista Socrático"
    descricao_estilo: "..."
    atributos_psicologicos:
      entusiasmo: 4
      ...
    comunicacao:
      tom_de_voz: "..."
      ...
    bagagem_cultural:
      ...
    diretrizes_de_comportamento: []
    vieses_e_restricoes: []

A referência passada em `--profile` pode ser:
  - um nome predefinido (ex.: "socratic" -> <profiles_dir>/socratic.yaml)
  - um caminho montado no container (ex.: "/profiles/meu-perfil.yaml")
"""
import os
import pathlib

import yaml


def _dir():
    return pathlib.Path(os.environ.get("AW_PROFILES_DIR", "/app/profiles"))


_ATTRS = [
    "entusiasmo",
    "formalidade",
    "confianca",
    "metodismo",
    "critica",
    "empatia",
    "jogo_de_cintura",
]


def _resolve(ref):
    p = pathlib.Path(ref)
    if p.exists():
        return p
    if ref.endswith(".yaml") or ref.endswith(".yml"):
        cand = _dir() / ref
    else:
        cand = _dir() / f"{ref}.yaml"
        if not cand.exists():
            cand = _dir() / f"{ref}.yml"
    if cand.exists():
        return cand
    raise FileNotFoundError(f"perfil não encontrado: {ref}")


def load_profile(ref):
    path = _resolve(ref)
    data = yaml.safe_load(path.read_text()) or {}
    return data


def _bullet(title, value):
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value)
    return f"- {title}: {value}"


def render(data):
    lines = []
    name = data.get("profile_name") or "Perfil"
    lines.append(f"## {name}")
    if data.get("descricao_estilo"):
        lines.append(f"\n{data['descricao_estilo']}")

    attrs = data.get("atributos_psicologicos") or {}
    if attrs:
        lines.append("\n## Atributos psicológicos (escala 0-10)")
        for key in _ATTRS:
            if key in attrs:
                lines.append(f"- {key.replace('_', ' ')}: {attrs[key]}")

    com = data.get("comunicacao") or {}
    if com:
        lines.append("\n## Comunicação")
        if com.get("tom_de_voz"):
            lines.append(f"- Tom de voz: {com['tom_de_voz']}")
        if com.get("palavras_chave"):
            lines.append(_bullet("Palavras-chave", com["palavras_chave"]))
        if com.get("expressoes_proibidas"):
            lines.append(_bullet("Expressões proibidas", com["expressoes_proibidas"]))
        if com.get("uso_de_emojis"):
            lines.append(f"- Uso de emojis: {com['uso_de_emojis']}")

    bagagem = data.get("bagagem_cultural") or {}
    if bagagem:
        lines.append("\n## Bagagem cultural e arquétipo")
        for key, label in [
            ("livros_inspiradores", "Livros inspiradores"),
            ("cinematografia_e_entretenimento", "Cinema/entretenimento"),
            ("animes_inspiradores", "Animes"),
            ("grandes_sagas", "Grandes sagas"),
            ("referencias_e_pessoas", "Referências e pessoas"),
            ("hobbies", "Hobbies"),
            ("musicas_e_ritmos", "Músicas e ritmos"),
        ]:
            if bagagem.get(key):
                lines.append(_bullet(label, bagagem[key]))
        if bagagem.get("arquetipo"):
            lines.append(f"- Arquétipo: {bagagem['arquetipo']}")

    diretrizes = data.get("diretrizes_de_comportamento") or []
    if diretrizes:
        lines.append("\n## Diretrizes de comportamento")
        for d in diretrizes:
            lines.append(f"- {d}")

    vieses = data.get("vieses_e_restricoes") or []
    if vieses:
        lines.append("\n## Vieses e restrições")
        for v in vieses:
            lines.append(f"- {v}")

    return "\n".join(lines).strip()
