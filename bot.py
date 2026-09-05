import os, json, hashlib
from pathlib import Path
import aiohttp
import discord
from deep_translator import GoogleTranslator

FEED_URL='https://grindmap.com/weekly.json'
TOKEN=os.environ['DISCORD_TOKEN']
CHANNEL_ID=int(os.environ['DISCORD_CHANNEL_ID'])
STATE=Path('state.json')
intents=discord.Intents.default()
client=discord.Client(intents=intents)

# Keep established GTA names; translate the surrounding descriptions.
KEEP=['GTA Online','GTA$','RP','LS Car Meet','GTA+','Rockstar Games','Karin Sultan Classic','Prize Ride']
def tr(s):
    if not s: return ''
    placeholders={}; out=s
    for i,x in enumerate(sorted(KEEP,key=len,reverse=True)):
        token=f'QZX{i}Q'
        if x in out: placeholders[token]=x; out=out.replace(x,token)
    try: out=GoogleTranslator(source='auto',target='de').translate(out)
    except Exception: return s
    for k,v in placeholders.items(): out=out.replace(k,v)
    return out

def load():
    try: return json.loads(STATE.read_text())
    except Exception: return {}
def save(x): STATE.write_text(json.dumps(x,ensure_ascii=False,indent=2))

def embed(d):
    e=discord.Embed(title='🎉 GTA ONLINE – EVENTWOCHE',description=tr(d.get('headline','Die neue Eventwoche ist da!')),colour=0x5865F2)
    e.add_field(name='📅 Zeitraum',value=tr(d.get('weekOf','Diese Woche')),inline=False)
    bs=[]
    for b in d.get('bonuses',[])[:8]: bs.append(f"**{b.get('multiplier',1)}×** {tr(b.get('method','Aktivität'))}\n↳ {tr(b.get('label','Bonus'))}")
    if bs:e.add_field(name='💰 GTA$ & RP – Boni',value='\n'.join(bs)[:1024],inline=False)
    ev=[]
    for x in d.get('events',[])[:8]: ev.append(f"**{tr(x.get('name',''))}**\n{tr(x.get('detail',''))}")
    if ev:e.add_field(name='🎯 Events & Aufgaben',value='\n\n'.join(ev)[:1024],inline=False)
    ds=[]
    for x in d.get('discounts',[])[:8]:
        # Do not reproduce weapon/gambling-related offers in the bot output.
        text=(x.get('name','')+' '+x.get('detail','')).lower()
        if any(w in text for w in ['gun van','rifle','weapon','gambling','table games']): continue
        ds.append(f"**{tr(x.get('name',''))}**\n{tr(x.get('detail',''))}")
    if ds:e.add_field(name='🏷️ Rabatte',value='\n\n'.join(ds)[:1024],inline=False)
    e.add_field(name='🚗 Podiumsfahrzeug',value=d.get('podiumVehicle','Nicht angegeben'),inline=False)
    e.set_footer(text='Daten: GrindMap • automatisch aktualisiert • Donnerstag-Reset')
    return e

async def main():
    async with aiohttp.ClientSession() as s:
        async with s.get(FEED_URL,headers={'User-Agent':'GTA-EventBot/1.0'}) as r:
            r.raise_for_status(); d=await r.json()
    key=hashlib.sha256((d.get('weekOf','')+'|'+d.get('updated','')).encode()).hexdigest()
    state=load()
    if state.get('key')==key: return
    ch=client.get_channel(CHANNEL_ID) or await client.fetch_channel(CHANNEL_ID)
    await ch.send(embed=embed(d))
    save({'key':key,'weekOf':d.get('weekOf'),'updated':d.get('updated')})

@client.event
async def on_ready():
    print('Bot online als',client.user)
    try: await main()
    finally: await client.close()

client.run(TOKEN)
