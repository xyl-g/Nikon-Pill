"""Rasterize a selected text subset into deterministic solid RECTs. No font is bundled."""
import argparse,hashlib,json,math,re,struct
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont,features
import PIL
ROOT=Path(__file__).resolve().parents[1]
def sha(b):return hashlib.sha256(b).hexdigest()
def tables(blob):
 n=struct.unpack_from('>H',blob,4)[0];return {tag.decode('ascii'):(off,length)for tag,cs,off,length in (struct.unpack_from('>4sIII',blob,12+16*i)for i in range(n))}
def names(blob,t):
 off,n=t['name'];fmt,count,start=struct.unpack_from('>3H',blob,off);out={}
 for i in range(count):
  platform,encoding,lang,ident,n,at=struct.unpack_from('>6H',blob,off+6+12*i)
  if ident not in (0,1,2,13,14,16,17):continue
  raw=blob[off+start+at:off+start+at+n]
  try:value=raw.decode('utf-16-be'if platform in (0,3)else'mac_roman')
  except UnicodeError:continue
  if ident not in out or lang==0x409:out[ident]=value
 return out
def has_glyph(blob,t,cp):
 off,n=t['cmap'];count=struct.unpack_from('>H',blob,off+2)[0]
 for i in range(count):
  platform,encoding,relative=struct.unpack_from('>HHI',blob,off+4+8*i);at=off+relative;fmt=struct.unpack_from('>H',blob,at)[0]
  if fmt==12:
   for j in range(struct.unpack_from('>I',blob,at+12)[0]):
    first,last,glyph=struct.unpack_from('>3I',blob,at+16+j*12)
    if first<=cp<=last and glyph+cp-first:return True
  elif fmt==4 and cp<=0xffff:
   segments=struct.unpack_from('>H',blob,at+6)[0]//2;ends=at+14;starts=ends+2*segments+2;deltas=starts+2*segments;ranges=deltas+2*segments
   for j in range(segments):
    first=struct.unpack_from('>H',blob,starts+2*j)[0];last=struct.unpack_from('>H',blob,ends+2*j)[0]
    if first<=cp<=last:
     delta=struct.unpack_from('>h',blob,deltas+2*j)[0];relative=struct.unpack_from('>H',blob,ranges+2*j)[0]
     glyph=(cp+delta)&65535 if not relative else struct.unpack_from('>H',blob,ranges+2*j+relative+2*(cp-first))[0]
     if relative and glyph:glyph=(glyph+delta)&65535
     if glyph:return True
 return False
def rects(mask):
 pixels=mask.load();active={};out=[]
 for y in range(mask.height):
  row=[];x=0
  while x<mask.width:
   if not pixels[x,y]:x+=1;continue
   start=x
   while x<mask.width and pixels[x,y]:x+=1
   row.append((start,x-start))
  keys=set(row)
  for key in list(active):
   if key not in keys:out.append(active.pop(key))
  for key in row:
   if key in active:active[key][3]+=1
   else:active[key]=[key[0],y,key[1],1]
 out.extend(active.values());return sorted(out,key=lambda r:(r[1],r[0],r[2],r[3]))
def generate(font_path,texts,size,weight,threshold,symbol,output):
 blob=font_path.read_bytes();tab=tables(blob);meta=names(blob,tab)
 if not re.search(r'Open Font License|OFL',meta.get(13,'')+' '+meta.get(14,''),re.I):raise ValueError('embedded OFL metadata required')
 font=ImageFont.truetype(str(font_path),size,layout_engine=ImageFont.Layout.BASIC)
 try:
  axes=font.get_variation_axes();values=[weight if a['name']==b'Weight'else a['default']for a in axes]
  for a,v in zip(axes,values):
   if not a['minimum']<=v<=a['maximum']:raise ValueError('variation outside font range')
  font.set_variation_by_axes(values)
 except OSError:axes=[];values=[]
 ascent,descent=font.getmetrics();height=ascent+descent
 lines=[];header=['/* Generated selected text bitmaps; no font binary or runtime UTF-8 decoder. */','#ifndef '+symbol.upper()+'_DATA_H','#define '+symbol.upper()+'_DATA_H','#include "fenestra_scene_text.h"']
 for ident,text in texts:
  if not text or len(text)>128 or any(ord(c)<32 or not has_glyph(blob,tab,ord(c))for c in text):raise ValueError('empty, unsupported or control text')
  box=font.getbbox(text,anchor='ls');left=min(0,box[0]);width=max(math.ceil(font.getlength(text)),box[2])-left
  if width>4096 or height>4096:raise ValueError('text exceeds scene dimensions')
  image=Image.new('L',(width,height));ImageDraw.Draw(image).text((-left,ascent),text,font=font,fill=255,anchor='ls')
  mask=image.point(lambda x:255 if x>=threshold else 0);runs=rects(mask)
  if not runs:raise ValueError('text has no visible pixels')
  name=symbol+'_'+ident.lower();header.append('static const FxSceneTextRect '+name+'[]={'+','.join('{'+','.join(map(str,r))+'}'for r in runs)+'};')
  lines.append(dict(id=ident,text=text,width=width,height=height,rectangles=len(runs),mask_sha256=sha(mask.tobytes()),rects=runs))
 header.append('enum {'+','.join(symbol.upper()+'_'+r['id']for r in lines)+','+symbol.upper()+'_COUNT};')
 header.append('static const FxSceneText '+symbol+'_texts[]={'+','.join('{'+str(r['width'])+','+str(r['height'])+','+str(r['rectangles'])+','+symbol+'_'+r['id'].lower()+'}'for r in lines)+'};')
 header.append('#endif');raw=('\n'.join(header)+'\n').encode();output.parent.mkdir(parents=True,exist_ok=True)
 with output.open('xb')as f:f.write(raw)
 report=dict(schema='fenestra.selected-scene-text/v1',font=dict(file=font_path.name,sha256=sha(blob),bytes=len(blob),names=meta,license='OFL-1.1',license_source='embedded font name records13/14',variations=values),raster=dict(size=size,weight=weight,threshold=threshold,Pillow=PIL.__version__,FreeType=features.version_module('freetype2'),layout='BASIC',antialias='thresholded monochrome',vertical_run_merge=True),texts=lines,codepoints=sorted({ord(c)for _,text in texts for c in text}),generated_header_sha256=sha(raw),scope='selected fixed strings only; ordinary scene RECTs, no core update, no full font, no runtime typography or physical-display qualification')
 output.with_suffix('.json').write_text(json.dumps(report,ensure_ascii=True,indent=2)+'\n');return report
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--font',type=Path,required=True);p.add_argument('--expected-font-sha');p.add_argument('--text',action='append',required=True,help='UPPERCASE_ID=text');p.add_argument('--size',type=int,default=24);p.add_argument('--weight',type=int,default=600);p.add_argument('--threshold',type=int,default=128);p.add_argument('--symbol',default='mascot_cjk');p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 if not 8<=a.size<=96 or not 1<=a.threshold<=255 or not re.fullmatch('[A-Za-z_][A-Za-z_0-9]*',a.symbol):raise ValueError('invalid raster options')
 if a.expected_font_sha and sha(a.font.read_bytes())!=a.expected_font_sha:raise ValueError('font SHA differs')
 texts=[]
 for value in a.text:
  ident,text=value.split('=',1)
  if not re.fullmatch('[A-Z][A-Z0-9_]*',ident)or any(x[0]==ident for x in texts):raise ValueError('invalid/duplicate text ID')
  texts.append((ident,text))
 out=a.output.resolve()
 if not out.is_relative_to(ROOT):raise ValueError('owned output required')
 r=generate(a.font,texts,a.size,a.weight,a.threshold,a.symbol,out);print(json.dumps({k:v for k,v in r.items()if k!='texts'}));print(json.dumps([{k:v for k,v in row.items()if k!='rects'}for row in r['texts']]))
if __name__=='__main__':main()
