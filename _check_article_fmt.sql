SELECT id, name,
       (regexp_replace(name, '^中华人民共和国', '') = '刑法') AS exact_short,
       (name LIKE '中华人民共和国' || '刑法' || '-%') AS split_entry,
       (name LIKE '中华人民共和国' || '刑法' || '%') AS full_prefix
FROM laws
WHERE '刑法' LIKE '%' || regexp_replace(name, '^中华人民共和国', '') || '%'
   OR name LIKE '%' || '刑法' || '%'
ORDER BY
  (regexp_replace(name, '^中华人民共和国', '') = '刑法') DESC,
  (name LIKE '中华人民共和国' || '刑法' || '-%') DESC,
  (name LIKE '中华人民共和国' || '刑法' || '%') DESC,
  length(name) ASC
LIMIT 6;
SELECT l.name, a.article_number FROM legal_articles a JOIN laws l ON a.law_id = l.id
WHERE a.article_number = '第一百三十三条之一';
