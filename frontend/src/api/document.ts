import service from './index'

export const documentApi = {
  getTemplates() {
    return service.get('/document/templates')
  },
  generateDocument(data: { template_type: string; parameters: Record<string, string>; language?: string }) {
    return service.post('/document/generate', data)
  },
  getHistory(page = 1, pageSize = 20) {
    // Note: Backend doesn't have a dedicated history endpoint yet
    // For now, we'll use the templates endpoint as a placeholder
    return service.get('/document/templates')
  },
  getDocument(documentId: string) {
    // Note: Backend doesn't have a dedicated get document endpoint yet
    // This is a placeholder
    return service.get(`/document/templates`)
  },
}
