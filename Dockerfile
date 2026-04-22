FROM python:3.9-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 下载官方接口协议文件并编译生成代码
ADD https://www.clouddrive2.com/api/clouddrive.proto .
RUN python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. clouddrive.proto

# 放入主程序和示例配置
COPY app.py .
COPY download-routes.example.yml .
RUN mkdir -p /config

EXPOSE 5000

CMD ["python", "-u", "app.py"]
