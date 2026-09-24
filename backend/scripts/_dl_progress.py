import json

cp = json.load(open('/tmp/flk_download_checkpoint.json'))
print('done=%d failed=%d' % (len(cp['done']), len(cp['failed'])))
