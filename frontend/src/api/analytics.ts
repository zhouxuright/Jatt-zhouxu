import request from './index'

export const analyticsApi = {
  getDashboard: (params?: { scope?: 'me' | 'global' }) =>
    request.get('/analytics/dashboard', { params }),
  getTopics: (params?: any) => request.get('/analytics/topics', { params }),
  getTrends: (params?: any) => request.get('/analytics/trends', { params }),
  getFeedbackSummary: () => request.get('/analytics/feedback-summary'),
}
