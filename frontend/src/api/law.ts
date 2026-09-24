import request from './index'

export const lawApi = {
  searchLaws: (data: { query: string; category?: string; top_k?: number }) =>
    request.post('/law/search', data),
  getCategories: () => request.get('/law/categories'),
  getArticles: (params?: any) => request.get('/law/articles', { params }),
}
