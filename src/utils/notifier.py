import os
import json
import logging
import requests
from dotenv import load_dotenv

# Carrega as variáveis de ambiente do ficheiro .env
load_dotenv()

logger = logging.getLogger(__name__)

class SlackNotifier:
    def __init__(self):
        self.webhook_url = os.getenv("SLACK_WEBHOOK_URL")
        if not self.webhook_url:
            logger.warning("SLACK_WEBHOOK_URL não configurada no .env. As notificações do Slack estão desativadas.")

    def send_message(self, message: str, level: str = "info"):
        """
        Envia uma mensagem para o Slack com formatação básica.
        level: 'info', 'warning', 'error', 'success'
        """
        if not self.webhook_url:
            return False

        # Emojis para dar contexto visual no Slack
        emojis = {
            "info": "ℹ️",
            "warning": "⚠️",
            "error": "🚨",
            "success": "✅"
        }
        emoji = emojis.get(level, "💬")
        
        # O Slack aceita formatação Markdown no campo text
        payload = {
            "text": f"{emoji} *Comex Data Product*\n{message}"
        }

        try:
            response = requests.post(
                self.webhook_url, 
                data=json.dumps(payload),
                headers={'Content-Type': 'application/json'}
            )
            
            if response.status_code != 200:
                logger.error(f"Falha ao enviar mensagem para o Slack. Status: {response.status_code}, Resposta: {response.text}")
                return False
                
            logger.info("Notificação enviada para o Slack com sucesso.")
            return True
            
        except Exception as e:
            logger.error(f"Erro na integração HTTP com o Slack: {e}")
            return False

# Instância global para facilitar a importação nos extratores
notifier = SlackNotifier()

if __name__ == "__main__":
    # Teste local rápido
    print("A iniciar o teste de envio para o Slack...")
    
    mensagem_teste = (
        "Olá! O webhook foi configurado com sucesso. 🚀\n"
        "O pipeline de Engenharia de Dados já consegue comunicar com este canal."
    )
    
    sucesso = notifier.send_message(mensagem_teste, "success")
    
    if sucesso:
        print("Mensagem disparada! Verifique o seu canal do Slack.")
    else:
        print("Falha ao enviar a mensagem. Verifique a variável SLACK_WEBHOOK_URL no ficheiro .env.")