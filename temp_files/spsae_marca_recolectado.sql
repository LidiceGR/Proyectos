
drop proc spsae_marca_recolectado
go
create proc spsae_marca_recolectado
            @id           int, 
			@clave_cambio varchar(30) ,
			@edificio     varchar(5),
			@piso         varchar(3),
			@recolectados varchar(300)
as
declare @status int , @num_cambio int, @max_oper int, @token varchar(100) , @edo_recoleccion char(1)

create table #recolectados ( num_inventario varchar(30) ) 
create table #INV_CAMBIO (num_inventario varchar(20), num_cambio int,  con_estado char(1),id int identity  ) 

if ( @id = -1 ) select @id = NULL

/*
** Se crea tabla de inventarios seleccionados
*/
select @recolectados  = isnull(rtrim(ltrim(upper(@recolectados ))),'')

while (@recolectados  != '')
      begin
      exec @status =spsae_solo_desconcatena_x_algo     '|', @recolectados  output,   @token  output
      insert #recolectados values(upper(@token) )
         if (@recolectados is NULL)   select @recolectados =''
      end

if not exists ( select 1 from #recolectados) return 50052

/*
** Inicia actualizacion 
*/

begin tran actRECOLECCION 

select @max_oper = isnull(max(operacion),0)+1 from OPERACION_ERP

insert into #INV_CAMBIO
   select num_inventario,  isnull(max(num_cambio),0)+1 ,'S'
     from EQUIPO_ESTADO 
	where edo_asignacion ='T'
	    and num_inventario in ( select num_inventario from RECOLECCION_EQUIPO where id = @id ) 
		and num_inventario in ( select num_inventario from #recolectados) 
  group by num_inventario 

insert into #INV_CAMBIO
   select num_inventario,  0,'N'
     from RECOLECCION_EQUIPO 
	where num_inventario in ( select num_inventario from #recolectados) 
      and num_inventario not in ( select num_inventario from 	#INV_CAMBIO)
   


select @edo_recoleccion='E'  
if exists ( select 1 from RECOLECCION_EQUIPO where recolectado = 'N' and id= @id and num_inventario not in ( select num_inventario from #recolectados) )
   begin
   select @edo_recoleccion='A'  
   end     
  

update RECOLECCION 
   set status       = @edo_recoleccion,
       fecha_cambio     = getdate(),
	   clave_cambio = @clave_cambio
 where id = @id 
 
select @status = @@error
if ( @status <> 0 ) 
   begin
   rollback actRECOLECCION
   return @status
   end 

update RECOLECCION_EQUIPO 
   set recolectado       = 'S'     
 where id = @id   
   and num_inventario  in ( select num_inventario from #recolectados) 
 
select @status = @@error
if ( @status <> 0 ) 
   begin
   rollback actRECOLECCION
   return @status
   end
      
insert into OPERACION_ERP
            ( operacion, tipo_operacion, num_inventario, clave_cambio, status, edificio, piso, f_proceso, f_registro) 
    select id+@max_oper, 'E',num_inventario,"",'A',@edificio,@piso,null,getdate() from #INV_CAMBIO 
	 
select @status = @@error
if ( @status <> 0 ) 
   begin
   rollback actRECOLECCION
   return @status
   end	 
      

insert into HST_EQUIPO_ASIGNADO
           ( clave_usuario,num_inventario,inv_nuevo,f_asignacion,tipo_soporte,edo_asignacion,personal,etiqueta,
			tipo_uso,consecutivo_pc,num_pc,status,f_cambio,clave_cambio )
	select clave_usuario, num_inventario, inv_nuevo, f_asignacion, tipo_soporte,'D',personal,etiqueta,
			tipo_uso,consecutivo_pc,num_pc,status,getdate(),@clave_cambio  
	 from  EQUIPO_ASIGNADO 
	where edo_asignacion ='T'
	  and num_inventario in ( select num_inventario from RECOLECCION_EQUIPO where id = @id  and num_inventario in ( select num_inventario from #recolectados)  ) 
	  
select @status = @@error
if ( @status <> 0 ) 
   begin
   rollback actRECOLECCION
   return @status
   end	 
   	  


insert into HST_SUSTITUCION 
            ( inv_sustituye,num_inventario,  clave_cambio, f_cambio ) 		
     select num_inventario, inv_nuevo,  @clave_cambio, getdate()  
	   from  EQUIPO_ASIGNADO 
	  where edo_asignacion ='T'
	    and num_inventario in ( select num_inventario from RECOLECCION_EQUIPO where id = @id and num_inventario in ( select num_inventario from #recolectados) ) 
	  
select @status = @@error
if ( @status <> 0 ) 
   begin
   rollback actRECOLECCION
   return @status
   end

    
insert into EQUIPO_ESTADO 
            ( num_cambio, num_inventario, edo_asignacion, f_cambio, clave_cambio ) 
     select num_cambio,num_inventario,'D', getdate(),@clave_cambio from #INV_CAMBIO where con_estado ='S'
            
   
select @status = @@error
if ( @status <> 0 ) 
   begin
   rollback actRECOLECCION
   return @status
   end
   
delete from EQUIPO_ASIGNADO  
       where num_inventario  in ( select num_inventario from RECOLECCION_EQUIPO where id = @id and num_inventario  in ( select num_inventario from #recolectados)) 
   
select @status = @@error
if ( @status <> 0 ) 
   begin
   rollback actRECOLECCION
   return @status
   end   
   
update EQUIPO_PRESTAMO 
   set f_fin = getdate()
 where    num_inventario  in ( select num_inventario from RECOLECCION_EQUIPO where id = @id and num_inventario  in ( select num_inventario from #recolectados) ) 
 
select @status = @@error
if ( @status <> 0 ) 
   begin
   rollback actRECOLECCION
   return @status
   end   
    

commit actRECOLECCION 

return 0 
go 
grant execute on spsae_marca_recolectado to web_app
go 



