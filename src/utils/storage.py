import os
import json
import glob
import logging
import sys
import boto3
import pandas as pd
from botocore.exceptions import ClientError
from dotenv import load_dotenv

# Garante o carregamento das variáveis do arquivo .env
load_dotenv()

logger = logging.getLogger(__name__)

class DataLakeConnector:
    def __init__(self):
        self.backend = os.getenv("STORAGE_BACKEND", "local").lower()
        self.bucket_name = os.getenv("AWS_S3_BUCKET")
        
        # Define a raiz do projeto para caminhos locais uniformes
        self.project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        
        if self.backend == "s3":
            if not self.bucket_name:
                raise ValueError("A variável AWS_S3_BUCKET não foi configurada no arquivo .env.")
            
            # O boto3 mapeia automaticamente AWS_ACCESS_KEY_ID e AWS_SECRET_ACCESS_KEY do ambiente
            self.s3_client = boto3.client("s3")
            logger.info(f"DataLakeConnector conectado à Nuvem AWS S3 (Bucket: {self.bucket_name})")
        else:
            logger.info("DataLakeConnector operando em modo de Armazenamento LOCAL")

    def _resolve_local_path(self, layer: str, service: str, filename: str) -> str:
        """Garante a existência da árvore de diretórios local e retorna o caminho."""
        directory = os.path.join(self.project_root, "data", layer, service)
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, filename)

    def _resolve_s3_key(self, layer: str, service: str, filename: str) -> str:
        """Garante a estrutura padronizada de chaves do Data Lake no S3."""
        return f"data/{layer}/{service}/{filename}"

    def save_parquet(self, df: pd.DataFrame, layer: str, service: str, filename: str) -> bool:
        """Grava um DataFrame do Pandas em formato colunar Parquet no backend configurado."""
        if self.backend == "s3":
            s3_path = f"s3://{self.bucket_name}/{self._resolve_s3_key(layer, service, filename)}"
            try:
                # O pandas utiliza a biblioteca s3fs de forma implícita para streams de rede S3
                df.to_parquet(s3_path, index=False)
                logger.info(f"Arquivo Parquet persistido no S3 com sucesso: {s3_path}")
                return True
            except Exception as e:
                logger.error(f"Falha na gravação do Parquet para o S3 ({s3_path}): {e}")
                return False
        else:
            local_path = self._resolve_local_path(layer, service, filename)
            try:
                df.to_parquet(local_path, index=False)
                logger.info(f"Arquivo Parquet persistido localmente com sucesso: {local_path}")
                return True
            except Exception as e:
                logger.error(f"Falha na gravação do Parquet local ({local_path}): {e}")
                return False

    def save_json(self, data: dict, layer: str, service: str, filename: str) -> bool:
        """Persiste estruturas de dicionários JSON diretamente na camada definida."""
        if self.backend == "s3":
            key = self._resolve_s3_key(layer, service, filename)
            try:
                self.s3_client.put_object(
                    Bucket=self.bucket_name,
                    Key=key,
                    Body=json.dumps(data, ensure_ascii=False, indent=4),
                    ContentType="application/json"
                )
                logger.info(f"Estrutura JSON enviada com sucesso para o S3: s3://{self.bucket_name}/{key}")
                return True
            except ClientError as e:
                logger.error(f"Falha no envio do objeto JSON para o S3: {e}")
                return False
        else:
            local_path = self._resolve_local_path(layer, service, filename)
            try:
                with open(local_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=4)
                logger.info(f"Estrutura JSON gravada localmente com sucesso: {local_path}")
                return True
            except Exception as e:
                logger.error(f"Falha na escrita do JSON local: {e}")
                return False
            
    def save_binary(self, data: bytes, layer: str, service: str, filename: str) -> bool:
        """Salva dados binários (como PDFs) no backend configurado."""
        if self.backend == "s3":
            key = self._resolve_s3_key(layer, service, filename)
            try:
                self.s3_client.put_object(Bucket=self.bucket_name, Key=key, Body=data)
                logger.info(f"Arquivo binário salvo com sucesso no S3: s3://{self.bucket_name}/{key}")
                return True
            except ClientError as e:
                logger.error(f"Falha no envio do binário para o S3: {e}")
                return False
        else:
            local_path = self._resolve_local_path(layer, service, filename)
            try:
                with open(local_path, "wb") as f:
                    f.write(data)
                logger.info(f"Arquivo binário salvo localmente com sucesso: {local_path}")
                return True
            except Exception as e:
                logger.error(f"Falha na escrita do binário local: {e}")
                return False

    def read_json(self, layer: str, service: str, filename: str) -> dict:
        """Recupera e decodifica documentos JSON do backend ativo."""
        if self.backend == "s3":
            key = self._resolve_s3_key(layer, service, filename)
            try:
                response = self.s3_client.get_object(Bucket=self.bucket_name, Key=key)
                return json.loads(response["Body"].read().decode("utf-8"))
            except ClientError as e:
                logger.error(f"Falha na leitura do JSON do S3 (Chave: {key}): {e}")
                return None
        else:
            local_path = self._resolve_local_path(layer, service, filename)
            if not os.path.exists(local_path):
                logger.error(f"Arquivo JSON local inexistente: {local_path}")
                return None
            try:
                with open(local_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Falha na leitura do JSON local: {e}")
                return None

    def obtain_file_path(self, layer: str, service: str, filename: str) -> str:
        """
        Garante a disponibilidade física do arquivo no disco.
        Caso o backend seja S3, realiza o download sob demanda. Útil para motores de processamento
        que necessitam de ponteiros de caminhos reais do sistema operacional (ex: pdfplumber).
        """
        local_path = self._resolve_local_path(layer, service, filename)
        
        if self.backend == "s3":
            key = self._resolve_s3_key(layer, service, filename)
            if not os.path.exists(local_path):
                try:
                    logger.info(f"Baixando recurso binário do S3 para cache de processamento: s3://{self.bucket_name}/{key}")
                    self.s3_client.download_file(self.bucket_name, key, local_path)
                except ClientError as e:
                    logger.error(f"Impossível resgatar binário do S3 ({key}): {e}")
                    return None
        return local_path
    
    def find_latest_file(self, layer: str, service: str, prefix: str, extension: str) -> str:
        """Encontra o ficheiro mais recente que corresponde a um prefixo e extensão."""
        if self.backend == "s3":
            prefix_key = f"data/{layer}/{service}/{prefix}"
            try:
                # Lista os objetos no S3 que começam com o nosso prefixo
                response = self.s3_client.list_objects_v2(Bucket=self.bucket_name, Prefix=prefix_key)
                if 'Contents' not in response:
                    return None
                
                # Filtra pela extensão e encontra o ficheiro mais recente
                files = [obj for obj in response['Contents'] if obj['Key'].endswith(extension)]
                if not files:
                    return None
                    
                latest_file = max(files, key=lambda x: x['LastModified'])
                return os.path.basename(latest_file['Key'])
            except Exception as e:
                logger.error(f"Erro ao procurar ficheiro no S3: {e}")
                return None
        else:
            local_dir = self._resolve_local_path(layer, service, "")
            search_pattern = os.path.join(local_dir, f"{prefix}*{extension}")
            files = glob.glob(search_pattern)
            
            if not files:
                return None
                
            latest_file = max(files, key=os.path.getctime)
            return os.path.basename(latest_file)

# Instanciação global para unificação de sessões
connector = DataLakeConnector()