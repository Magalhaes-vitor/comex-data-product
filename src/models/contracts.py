from pydantic import BaseModel, Field, field_validator
from typing import Literal
from typing import Optional 
from pydantic import BaseModel, Field

class RegistroComexMdic(BaseModel):
    ano: str = Field(..., description="Ano de referência do registro")
    mes: str = Field(..., description="Mês de referência do registro")
    fluxo: str = Field(..., description="Fluxo comercial: export ou import")
    pais: str = Field(..., alias="country", description="País parceiro comercial")
    valor_fob_usd: float = Field(..., alias="metricFOB", description="Valor FOB em dólares")
    peso_kg: float = Field(..., alias="metricKG", description="Peso em quilogramas")
    coNcm: Optional[str] = None
    ncm: Optional[str] = None
    co_ncm: Optional[str] = None

    @field_validator('fluxo')
    @classmethod
    def validar_fluxo(cls, v: str) -> str:
        if v.lower() not in ['export', 'import']:
            raise ValueError("O fluxo deve ser estritamente 'export' ou 'import'")
        return v.lower()

    @field_validator('valor_fob_usd', 'peso_kg', mode='before')
    @classmethod
    def tratar_numericos(cls, v):
        """Garante a conversão limpa de strings ou nulos para float."""
        if v is None or str(v).strip() in ['-', '', 'None']:
            return 0.0
        if isinstance(v, str):
            return float(v.replace('.', '').replace(',', '.'))
        return float(v)

class MovimentacaoPortuaria(BaseModel):
    """
    Contrato de Dados para a camada Silver. 
    Define o schema exato que esperamos extrair do PDF da APS.
    """
    ano: int = Field(..., description="Ano de referência da movimentação (ex: 2026)")
    mes: str = Field(..., description="Mês de referência (ex: mai)")
    mercadoria: str = Field(..., description="Nome da carga/mercadoria extraída da linha da tabela")
    tipo_operacao: str = Field(..., description="Ex: Exportação, Importação")
    volume_toneladas: float = Field(..., description="Volume físico movimentado em toneladas")

    @field_validator('volume_toneladas', mode='before')
    def clean_number_format(cls, value):
        """
        Trata o padrão brasileiro de números em PDFs (ex: '1.450.320,55' -> 1450320.55)
        antes de tentar converter para float.
        """
        if isinstance(value, str):
            value = value.strip()
            if value == '-' or value == '':
                return 0.0
            value = value.replace('.', '').replace(',', '.')
        
        try:
            return float(value)
        except ValueError:
            raise ValueError(f"Data Drift Detectado: Não foi possível converter '{value}' para número.")

    @field_validator('mes')
    def validate_mes(cls, value):
        """Garante que o mês esteja no formato curto padrão de 3 letras minúsculas."""
        meses_validos = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']
        val_limpo = str(value).lower().strip()
        
        if val_limpo not in meses_validos:
            raise ValueError(f"Data Drift Detectado: Mês '{value}' fora do padrão esperado.")
        return val_limpo


class CotacaoBacen(BaseModel):
    """
    Contrato de Dados para a camada Silver do Banco Central.
    Garante que as cotações financeiras cheguem formatadas e sem nulos.
    """
    data_cotacao: str = Field(..., description="Data da cotação no formato YYYY-MM-DD")
    cotacao_compra: float = Field(..., description="Valor de compra do Dólar (PTAX)")
    cotacao_venda: float = Field(..., description="Valor de venda do Dólar (PTAX)")

    @field_validator('data_cotacao', mode='before')
    def extract_date(cls, value):
        """
        O Bacen retorna data e hora (ex: '2026-05-04 13:09:25.789').
        Nós precisamos apenas da data (YYYY-MM-DD) para fazer o cruzamento futuro.
        """
        if isinstance(value, str) and ' ' in value:
            return value.split(' ')[0]
        return value

class RegistroConab(BaseModel):
    ano_referencia: str = Field(..., description="Ano de referência do registro")
    mes_referencia: str = Field(..., description="Mês de referência do registro")
    estado: str = Field(..., description="Sigla da UF (Unidade Federativa)")
    cultura: str = Field(..., description="Nome da cultura padronizada com o APS")
    producao_mil_t: float = Field(..., description="Produção estimada na safra atual (em mil toneladas)")

    @field_validator('estado')
    @classmethod
    def validar_estado(cls, v: str) -> str:
        ufs = ['AC','AL','AM','AP','BA','CE','DF','ES','GO','MA','MG','MS','MT','PA','PB','PE','PI','PR','RJ','RN','RO','RR','RS','SC','SE','SP','TO']
        if v.upper() not in ufs:
            raise ValueError("Estado inválido")
        return v.upper()

    @field_validator('producao_mil_t', mode='before')
    @classmethod
    def tratar_numericos(cls, v):
        """Garante que a produção, mesmo que venha em branco ou com hífen, vire um float."""
        if v is None or str(v).strip() in ['-', '', 'None', 'nan', 'NaN']:
            return 0.0
        if isinstance(v, str):
            return float(v.replace('.', '').replace(',', '.'))
        return float(v)