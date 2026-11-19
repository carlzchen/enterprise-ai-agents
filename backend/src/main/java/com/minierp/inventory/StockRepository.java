package com.minierp.inventory;

import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;

import java.util.Optional;

public interface StockRepository extends JpaRepository<Stock, Long> {

    /** SELECT ... FOR UPDATE：同一物料的出入库串行化，防止并发超卖与成本计算错乱 */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select s from Stock s where s.warehouseId = ?1 and s.itemType = ?2 and s.itemId = ?3")
    Optional<Stock> lockOne(Long warehouseId, String itemType, Long itemId);

    Optional<Stock> findByWarehouseIdAndItemTypeAndItemId(Long warehouseId, String itemType, Long itemId);
}
