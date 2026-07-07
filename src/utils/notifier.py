import logging

logger = logging.getLogger(__name__)

class Notifier:
    @staticmethod
    def send_alert(pipeline_name: str, error_message: str):
        """
        Registra um alerta crítico de falha no pipeline.
        Ponto de extensão para integrações futuras com webhooks (Slack/Teams).
        """
        alert_msg = (
            f"\n"
            f"ALERTA CRÍTICO\n"
            f"Pipeline: {pipeline_name}\n"
            f"Erro: {error_message}\n"
            f"Ação necessária: Verificar fonte de dados ou conectividade.\n"
        )
        
        logger.critical(alert_msg)

if __name__ == "__main__":
    Notifier.send_alert("TesteNotifier", "Simulação de falha de conexão.")