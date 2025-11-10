#!/bin/bash
# Script para configurar el menú de comandos del bot de Telegram
# Se ejecuta automáticamente después del deploy o manualmente con: bash scripts/setup_bot_commands.sh

set -euo pipefail

# Cargar variables de entorno
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

BOT_TOKEN="${TELEGRAM_BOT_TOKEN}"

if [ -z "$BOT_TOKEN" ]; then
    echo "❌ ERROR: TELEGRAM_BOT_TOKEN no está configurado"
    exit 1
fi

echo "🤖 Configurando menú de comandos del bot..."

# Configurar comandos usando la API de Telegram
curl -X POST "https://api.telegram.org/bot${BOT_TOKEN}/setMyCommands" \
  -H "Content-Type: application/json" \
  -d '{
    "commands": [
      {
        "command": "start",
        "description": "🚀 Iniciar bot y ver bienvenida"
      },
      {
        "command": "help",
        "description": "📚 Ver todos los comandos disponibles"
      },
      {
        "command": "g",
        "description": "🎯 Generar tweet (Gemini 2.0)"
      },
      {
        "command": "g1",
        "description": "⚡ Tweet con DeepSeek (rápido)"
      },
      {
        "command": "g2",
        "description": "💎 Tweet con Gemini 2.5 Pro"
      },
      {
        "command": "g3",
        "description": "🎨 Tweet con Claude Opus 4.1"
      },
      {
        "command": "g4",
        "description": "🔷 Tweet con GPT-4o"
      },
      {
        "command": "c",
        "description": "💬 Generar comentario para post"
      },
      {
        "command": "tema",
        "description": "➕ Agregar tema nuevo"
      },
      {
        "command": "temas",
        "description": "📋 Ver últimos 10 temas"
      },
      {
        "command": "pdfs",
        "description": "📊 Ver estadísticas de publicaciones"
      },
      {
        "command": "ping",
        "description": "🔧 Verificar conexión con BD"
      }
    ]
  }' | jq '.'

echo "✅ Menú de comandos configurado exitosamente"
