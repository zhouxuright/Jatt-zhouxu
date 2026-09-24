import request from './index'

export const analyticsApi = {
  getDashboard: () => request.get('/analytics/dashboard'),
  getTopics: (params?: any) => request.get('/analytics/topics', { params }),
  getTrends: (params?: any) => request.get('/analytics/trends', { params }),
  getFeedbackSummary: () => request.get('/analytics/feedback-summary'),
}
