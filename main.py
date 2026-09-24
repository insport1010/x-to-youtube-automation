import json,os,re,subprocess,tempfile,time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
HANDLE=os.getenv('X_HANDLE','FConPredict');STATE=Path(os.getenv('STATE_FILE','state.json'));TZ=ZoneInfo(os.getenv('TIMEZONE','Africa/Cairo'));MAX_DAILY=6;SCOPES=['https://www.googleapis.com/auth/youtube.upload']
def state():return json.loads(STATE.read_text()) if STATE.exists() else {'uploaded':[],'day':'','count':0}
def cookies_file(p):
 rows=['# Netscape HTTP Cookie File']
 for c in json.loads(os.getenv('X_COOKIES_JSON','[]')):
  if 'name' in c and 'value' in c: rows.append('\t'.join([c.get('domain','.x.com'),'TRUE' if c.get('domain','.x.com').startswith('.') else 'FALSE',c.get('path','/'),'TRUE' if c.get('secure') else 'FALSE',str(int(c.get('expirationDate',0) or 0)),c['name'],c['value']]))
 Path(p).write_text('\n'.join(rows),encoding='utf-8')
def fetch_candidates():
 o=Options();o.add_argument('--headless=new');o.add_argument('--no-sandbox');o.add_argument('--disable-dev-shm-usage');o.add_argument('--window-size=1280,1200');o.add_argument('--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36');o.add_argument('--disable-blink-features=AutomationControlled');d=webdriver.Chrome(options=o)
 try:
  d.get('https://x.com/');time.sleep(3)
  for c in json.loads(os.getenv('X_COOKIES_JSON','[]')):
   try:d.add_cookie({k:c[k] for k in ('name','value','path','secure','httpOnly') if k in c})
   except:pass
  d.refresh();time.sleep(4);d.get(f'https://x.com/{HANDLE}');time.sleep(12)
  print('X page:',d.current_url,'title=',d.title,'articles=',len(d.find_elements(By.CSS_SELECTOR,'article')))
  ids=[]
  for _ in range(6):
   for a in d.find_elements(By.CSS_SELECTOR,'article'):
    try:
     m=re.search(r'/status/(\d+)',a.find_element(By.CSS_SELECTOR,"a[href*='/status/']").get_attribute('href') or '')
     if m:ids.append(m.group(1))
    except:pass
   d.execute_script('window.scrollBy(0,1600)');time.sleep(3)
  print('Found',len(set(ids)),'candidate posts');return list(dict.fromkeys(ids))
 finally:d.quit()
def youtube():return build('youtube','v3',credentials=Credentials.from_authorized_user_info(json.loads(os.environ['YOUTUBE_TOKEN_JSON']),SCOPES))
def upload(api,f,t,d):return api.videos().insert(part='snippet,status',body={'snippet':{'title':t[:100],'description':d},'status':{'privacyStatus':'public'}},media_body=MediaFileUpload(f,resumable=True)).execute()['id']
def short(f):
 p=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration:stream=width,height','-of','json',f],capture_output=True,text=True)
 if p.returncode:return False
 x=json.loads(p.stdout);v=next((s for s in x.get('streams',[]) if s.get('width') and s.get('height')),None);return bool(v and float(x.get('format',{}).get('duration',0)or 0)<=180 and v['height']>=v['width'])
def main():
 n=datetime.now(TZ)
 if not(12<=n.hour<=23)and os.getenv('ALLOW_OUT_OF_WINDOW')!='1':return
 s=state();today=n.date().isoformat()
 if s.get('day')!=today:s={'uploaded':s.get('uploaded',[]),'day':today,'count':0}
 if s['count']>=MAX_DAILY:return
 api=youtube()
 for pid in fetch_candidates():
  if pid in s['uploaded'] or s['count']>=MAX_DAILY:continue
  u=f'https://x.com/{HANDLE}/status/{pid}'
  with tempfile.TemporaryDirectory()as td:
   cf=str(Path(td)/'cookies.txt');cookies_file(cf);out=str(Path(td)/'video.%(ext)s')
   r=subprocess.run(['yt-dlp','--cookies',cf,'--no-warnings','--print','description','-o',out,u],capture_output=True,text=True,timeout=180);fs=list(Path(td).glob('video.*'))
   print('Download',pid,'exit',r.returncode)
   if r.returncode or not fs or not short(str(fs[0])):continue
   vid=upload(api,str(fs[0]),re.sub(r'\s+',' ',r.stdout.strip())or f'FConPredict video {pid}',(r.stdout.strip()+'\n\nSource: '+u).strip());print('Uploaded',vid)
  s['uploaded'].append(pid);s['count']+=1
 STATE.write_text(json.dumps(s,indent=2))
if __name__=='__main__':main()
