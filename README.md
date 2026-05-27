# 💑 Hub Casal

Dashboard pessoal de casal com sistema de competição. Bernardo e Amanda têm seus próprios perfis, dados de saúde, tarefas e hábitos — e competem por pontos diários.

## Como funciona

Cada pessoa faz login com seu próprio usuário e vê o próprio dashboard. Qualquer um pode **visualizar** o perfil do parceiro, mas só pode **editar** o próprio. O placar de pontos fica visível para ambos em tempo real.

### Pontuação diária

| Ação | Pontos |
|------|--------|
| ✅ Tarefa concluída | +2 pts cada |
| 🔥 Hábito do dia | +3 pts cada |
| 👟 1.000 passos | +1 pt (máx 10) |
| 💧 ≥ 2L de água | +2 pts |
| 😴 ≥ 7h de sono | +3 pts |
| 😊 Humor registrado | +1 pt |

## Setup

### 1. Pré-requisitos

- Python 3.11+
- `pip install -r hub/requirements.txt`

### 2. Configuração

```bash
cp .env.example .env
# Edite .env com suas senhas e chaves
```

### 3. Google Health API (opcional)

Para cada usuário que quiser sincronizar dados do Google Fit / Fitbit:

```bash
# Coloque o credentials.json em hub/data/{usuario}/credentials.json
python hub/hub_generator.py --auth-health --user bernardo
python hub/hub_generator.py --auth-calendar --user bernardo

python hub/hub_generator.py --auth-health --user amanda
python hub/hub_generator.py --auth-calendar --user amanda
```

### 4. Iniciar

```bash
python hub/server.py
# Abre automaticamente em http://localhost:5000
```

## Estrutura

```
hub-casal/
├── hub/
│   ├── server.py          # Servidor HTTP + API REST
│   ├── hub_generator.py   # Busca dados de saúde, notícias, briefing
│   ├── hub.html           # SPA — interface do casal
│   └── data/
│       ├── bernardo.db    # Tarefas, hábitos, humor do Bernardo
│       ├── amanda.db      # Tarefas, hábitos, humor da Amanda
│       ├── bernardo/      # JSONs de saúde/notícias do Bernardo
│       └── amanda/        # JSONs de saúde/notícias da Amanda
├── .env.example
└── README.md
```
