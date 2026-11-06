import express from 'express';
import './init-db.mjs'; // DB 초기화 및 테이블 확인 로직 실행

const app = express();
const port = process.env.PORT || 3000;

app.use(express.json());

app.get('/', (req, res) => {
  res.send('Music Platform API Server with SQLite Specification is Running!');
});

app.listen(port, () => {
  console.log(`Server is running on port ${port}`);
});