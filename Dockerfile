FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8000
ENV HOST=0.0.0.0
ENV NEXTJS_API_BASE=http://ltmap:3000

EXPOSE 8000

CMD ["python", "run.py"]
