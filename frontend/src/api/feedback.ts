import service from './index'

export const feedbackApi = {
  submitFeedback(data: { message_id?: string; rating: number; comment?: string; feedback_type?: string }) {
    return service.post('/feedback/feedback', data)
  },
  getMyFeedback(page = 1, pageSize = 20) {
    return service.get('/feedback/feedback/stats')
  },
}

export const lawApi = {
  searchLaws(data: { query: string; category?: string; top_k?: number }) {
    return service.post('/law/search', data)
  },
  getCategories() {
    return service.get('/law/categories')
  },
  getArticles(category?: string) {
    return service.get('/law/articles', { params: { category } })
  },
}

export const analyticsApi = {
  getDashboard() {
    return service.get('/analytics/dashboard')
  },
  getTopics(period = '30d', limit = 10) {
    return service.get('/analytics/topics', { params: { period, limit } })
  },
  getTrends(period = '30d', granularity = 'day') {
    return service.get('/analytics/trends', { params: { period, granularity } })
  },
  getFeedbackSummary() {
    return service.get('/analytics/feedback-summary')
  },
}
