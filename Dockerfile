# 1. Usa a imagem oficial do AWS Lambda para Python
FROM public.ecr.aws/lambda/python:3.10

# 2. Instala dependências de SO necessárias para C++ e processamento de imagens
RUN yum update -y && \
    yum install -y gcc gcc-c++ make rust cargo zlib-devel libjpeg-devel

# 3. Copia o arquivo de dependências do Python
COPY requirements.txt ${LAMBDA_TASK_ROOT}

# 4. Atualiza os motores de instalação do Python
RUN pip install --upgrade pip setuptools wheel

# 5. Instala as bibliotecas do projeto cravadas nas versões estáveis
RUN pip install --no-cache-dir -r requirements.txt

# 6. Copia o cérebro do projeto (a pasta src) e o arquivo de gatilho
COPY src/ ${LAMBDA_TASK_ROOT}/src/
COPY lambda_function.py ${LAMBDA_TASK_ROOT}/

# 7. Copia o .env (Embora no Lambda seja melhor setar via console, deixamos por segurança)
COPY .env ${LAMBDA_TASK_ROOT}/

# 8. Aponta para o Lambda qual função ele deve chamar quando for acionado
CMD [ "lambda_function.lambda_handler" ]