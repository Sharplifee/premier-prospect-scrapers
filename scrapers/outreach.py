"""
Outreach sender — drains pp_outreach_queue through Twilio.

Two hard gates, in this order:
  1. The Twilio A2P campaign must be APPROVED (checked live against Twilio each
     run). If it isn't, nothing sends and the run says so. US carriers drop
     unregistered A2P traffic; sending anyway would burn the number.
  2. Only rows that are due, queued, and for a phone that has not opted out.

Every successful send writes a lifecycle touch ('sms_sent'), so the Work list
knows the lead has been contacted and stops surfacing it as 'never contacted'.
Runs after enrichment. Idempotent: a row is 'sent' or 'failed' exactly once.
"""
import os, re, json, time, logging, requests
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s'); log=logging.getLogger('outreach')
SB=os.environ['SUPABASE_URL'].rstrip('/'); KEY=os.environ['SUPABASE_SERVICE_KEY']
H={'apikey':KEY,'Authorization':'Bearer '+KEY,'Content-Type':'application/json'}
SID=os.environ.get('TWILIO_ACCOUNT_SID',''); TOK=os.environ.get('TWILIO_AUTH_TOKEN',''); MSID=os.environ.get('TWILIO_MESSAGING_SERVICE_SID',''); CAMPAIGN=os.environ.get('TWILIO_A2P_CAMPAIGN_SID','')
MAX_PER_RUN=int(os.environ.get('SMS_MAX_PER_RUN','40'))

def campaign_approved():
    """Gate 1. Ask Twilio, don't assume."""
    if not (SID and TOK and MSID and CAMPAIGN): log.error('Twilio env incomplete — refusing to send'); return False
    r=requests.get(f'https://messaging.twilio.com/v1/Services/{MSID}/Compliance/Usa2p/{CAMPAIGN}',auth=(SID,TOK),timeout=30)
    if r.status_code!=200: log.error(f'campaign lookup {r.status_code}: {r.text[:120]} — refusing to send'); return False
    st=r.json().get('campaign_status','').upper(); log.info(f'A2P campaign status: {st}')
    return st=='VERIFIED'

def send(row):
    r=requests.post(f'https://api.twilio.com/2010-04-01/Accounts/{SID}/Messages.json',auth=(SID,TOK),
                    data={'To':row['to_phone'],'MessagingServiceSid':MSID,'Body':row['body']},timeout=30)
    if r.status_code in (200,201): return r.json().get('sid'), None
    return None, f"{r.status_code} {r.text[:140]}"

def main():
    if not campaign_approved():
        n=len(requests.get(f"{SB}/rest/v1/pp_outreach_queue?select=id&status=eq.queued&due_at=lte.now()",headers=H,timeout=30).json())
        log.info(f'{n} message(s) due but the carrier campaign is not verified — nothing sent'); return 0
    due=requests.get(f"{SB}/rest/v1/pp_outreach_queue?select=*&status=eq.queued&due_at=lte.now()&order=due_at.asc&limit={MAX_PER_RUN}",headers=H,timeout=30).json()
    log.info(f'{len(due)} message(s) due')
    sent=0
    for row in due:
        # gate 2: the number may have opted out since this was queued
        st=requests.get(f"{SB}/rest/v1/pp_lead_lifecycle?select=state&entity_key=eq.{requests.utils.quote(row['entity_key'])}",headers=H,timeout=30).json()
        if st and st[0]['state'] in ('do_not_contact','dead','not_interested','sold','listed_elsewhere','listed_with_us'):
            requests.patch(f"{SB}/rest/v1/pp_outreach_queue?id=eq.{row['id']}",json={'status':'halted','error':'lifecycle '+st[0]['state']},headers=H,timeout=30); continue
        sid,err=send(row)
        if sid:
            requests.patch(f"{SB}/rest/v1/pp_outreach_queue?id=eq.{row['id']}",json={'status':'sent','sent_at':'now()','provider_sid':sid},headers=H,timeout=30)
            requests.post(f"{SB}/rest/v1/rpc/pp_record_outcome",json={'p_entity_key':row['entity_key'],'p_event_type':'sms_sent','p_outcome':'attempted','p_channel':'sms','p_actor':'outreach','p_notes':f"step {row['step']} ({row['situation']})"},headers=H,timeout=30)
            sent+=1; log.info(f"  sent step {row['step']} to …{row['to_phone'][-4:]} ({row['situation']})")
        else:
            requests.patch(f"{SB}/rest/v1/pp_outreach_queue?id=eq.{row['id']}",json={'status':'failed','error':err},headers=H,timeout=30); log.error(f"  failed …{row['to_phone'][-4:]}: {err}")
        time.sleep(1.1)   # carrier-friendly pacing
    log.info(f'sent {sent}'); return sent

if __name__=='__main__': main()
