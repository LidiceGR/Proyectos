use asigna_etb
go
drop proc spsae_cons_usuequipo_citas
go
create proc spsae_cons_usuequipo_citas
            @depto           varchar(4)  = NULL, 
			@edificio        varchar(5)  = NULL,
			@clav_nom        varchar(60) = NULL,            
            @inventario_act  varchar(20) = NULL,			
			@evento          int         = NULL,
			@f_cita          datetime    = NULL,		
            @folio_sc        int         = NULL,
            @automatico      char(1)     = NULL			
as


declare @status int, @token varchar ( 50 ) 

if ( @evento   = -1 ) select @evento   = NULL
if ( @folio_sc = -1 ) select @folio_sc = NULL


if (     ( @depto       is NULL ) AND ( @edificio       is NULL ) AND ( @clav_nom is NULL ) 
     AND ( @inventario_act is NULL ) AND ( @evento   is NULL ) 
	 and ( @f_cita      is NULL ) and ( @folio_sc is NULL ) and ( @automatico is NULL )  ) 
	 begin
	 select 1 where 1= 2
	 return 0 
	 end
	 

select @clav_nom  = isnull(rtrim(ltrim(upper(@clav_nom ))),'')

create table #filtro ( filtro varchar(100),   longitud  int )

while (@clav_nom  != '')
      begin
      exec @status =spsae_desconcatena_x_espacio     @clav_nom  output,   @token  output
      insert #filtro values(upper(@token), char_length(rtrim(ltrim(@token))) )
         if (@clav_nom is NULL)   select @clav_nom =''
      end

declare cursor_filtro cursor  for select filtro  from #filtro  order by longitud desc for read only

open cursor_filtro

select @token  =''
       
fetch cursor_filtro into  @token         

select u.departamento, d.descripcion desc_departamento,
       u.apellido_paterno, u.apellido_materno, u.nombre,	
	   e.num_inventario,
	   e.tipo_equipo,
	   e.serie,
	   e.estado_erp,
	   e.marca,
	   e.modelo,
	   e.edificio,
	   e.piso,
	   c.cita, 
       c.folio_sc,
	   c.clave_cambio,
	   u.clave_usuario,
	   convert(char(10),c.fecha_original,103) fecha_original,
	   convert(char(5),c.fecha_original,108) hora_original,
	   convert(char(10),c.fecha_actual,103) fecha_actual,
	   convert(char(5),c.fecha_actual,108) hora_actual,
	   c.fecha_actual fecha_actual_filtro,
	   c.tipo_cita,
	   c.inv_actual,
	   c.inv_nuevo, 
	   c.estado,
	   c.evento, 	  
	   ec.descripcion desc_estado,
	   t.descripcion desc_tipo_cita, 
	   v.nombre      nom_evento,
	   a.edo_asignacion, 	   
	   g.descripcion desc_edo_asignacion,
      g.descripcion2 desc2_edo_asignacion,
	   
	   a.por_sustituir,
	   a.tipo_uso,
	   s.descripcion desc_tipo_uso,
	   a.etiqueta,
	   a.personal
    into #resultado  	  
  from USUARIO      u ,
       DEPARTAMENTO d,
	   EQUIPO       e,
	   CITA         c,
	   ESTADO_CITA   ec, 
	   TIPO_CITA     t, 
	   EVENTO_SC     v,
	   EQUIPO_ASIGNADO a,
	   ESTADO_ASIGNACION g,
	   TIPO_USO s
  
 where ( ( @token = ''    )  OR ( upper(u.clave_usuario)+' '+upper(u.apellido_paterno)+' '+upper(u.apellido_materno)+' '+upper(u.nombre) like '%'+@token+'%' ) )        
   and u.departamento = d.departamento 	   
   and u.clave_usuario = e.responsable
   and ( ( @edificio is NULL ) OR (  edificio = @edificio ))
   and ( ( @folio_sc is NULL ) OR ( e.num_inventario in ( select inv_actual from CITA where folio_sc= @folio_sc)))
   and ( ( @automatico is NULL ) OR ( e.num_inventario in ( select inv_actual from CITA where  @automatico is NULL OR clave_cambio = 'JOB_AJUSTE')))
   and e.num_inventario *= c.inv_actual
   and c.estado       *= ec.estado
   and c.tipo_cita    *= t.tipo_cita
   and c.evento       *= v.evento
   and e.num_inventario    *= a.num_inventario
   and a.edo_asignacion *= g.edo_asignacion
   and a.tipo_uso *= s.tipo_uso       
   and ( ( @depto       is NULL ) OR ( u.departamento = @depto       ) )    
   and ( ( @inventario_act is NULL ) OR ( e.num_inventario like '%'+@inventario_act+'%' ) )    
 
   and (  e.tipo_equipo in ( select tipo_equipo from TIPO_EQUIPO_SAE where tipo_filtro = 'C'))
   --and ( e.num_inventario in ( select num_inventario from EQUIPO_ASIGNADO where por_sustituir= 'S' ) ) 
      
      
  
	   
	   
	     
   
  
   
   
fetch cursor_filtro into  @token

while (@token != '')
      begin      
      delete #resultado  where upper(clave_usuario)+' '+upper(apellido_paterno)+' '+upper(apellido_materno)+' '+upper(nombre) not like '%'+ @token +'%'
      select @token =''
      fetch cursor_filtro into @token
      end

close cursor_filtro
deallocate   cursor cursor_filtro

select r.*,
       sc.id_siscitas,
		sc.clave_siscitas,
		sc.url_siscitas,
        sc.publico sc_publico, 
        sc.activo sc_activo
 from #resultado r,
      EVENTO_SC sc,
      EVENTO_EDIFICIO f	  
 where ( ( @evento         is NULL ) OR ( r.evento  = @evento ) ) 
   and ( ( @f_cita         is NULL ) OR ( convert(datetime, convert(char(10),fecha_actual_filtro,111)) = @f_cita ))         
   and r.edificio *= f.edificio
   and f.evento *= sc.evento
order by clave_usuario, num_inventario



return 0 
go
grant execute on spsae_cons_usuequipo_citas to web_app
go

      