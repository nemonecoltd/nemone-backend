module.exports = {
  apps: [{
    name: 'backend',
    // 가상환경 내의 python3를 사용하여 uvicorn을 모듈로 실행
    script: '/home/nemonecoltd/nemone-network/backend/venv/bin/python3',
    args: '-m uvicorn main:app --host 0.0.0.0 --port 8080 --proxy-headers',
    
    // 작업 디렉토리 명시
    cwd: '/home/nemonecoltd/nemone-network/backend',
    
    // 환경 변수 설정
    env: {
      NODE_ENV: 'production',
      PYTHONPATH: '.',
      // 필요한 환경 변수가 있다면 여기에 추가 (예: DB_URL)
    },
    
    // 서버 재시작 시 자동 실행 및 로그 설정
    autorestart: true,
    watch: false,
    max_memory_restart: '500M',
    
    // 로그 파일 경로 지정 (관리 용이성)
    error_file: './logs/error.log',
    out_file: './logs/access.log',
    log_date_format: 'YYYY-MM-DD HH:mm:ss'
  }]
};